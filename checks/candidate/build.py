"""Build the test installer from the released, hash-pinned R3 assets and a code delta."""
import argparse
import ast
from copy import copy
from difflib import SequenceMatcher
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import struct
import subprocess
import zipfile


HERE = Path(__file__).resolve().parent
BASE = '433a4e27a3ea0e93d6b13d5982f8cfc19ca6cfd0e4f3e23cff1af27b5db8e3d7'
APP = 'f707ca83d3f838ebc9cd22bf503673b29f563bb809e825ef6a37e3c0f5a1a404'
RUNTIME = 'cfe08204405352033f8fc67b1ecaa1478f7466d017be039bff5d1af390597259'
LAUNCHER = '9e595579f879cc968126d6e3d5655ff1a849027b7ef29a34e454df10a08b4d91'
sha = lambda data: hashlib.sha256(data).hexdigest()


def resource(pe, size, head, digest):
    prefix = struct.pack('<I', size) + head
    start = pe.find(prefix)
    if start < 0 or pe.find(prefix, start + 1) >= 0:
        raise ValueError('Missing or ambiguous managed resource')
    value = pe[start+4:start+4+size]
    if sha(value) != digest:
        raise ValueError('Managed resource has the wrong hash')
    return value


def module_name(name):
    path = PurePosixPath(name)
    if (str(path) != name or not name.isascii() or name.startswith('/')
            or any(not part.isidentifier() for part in path.with_suffix('').parts)
            or path.suffix != '.py' or '..' in path.parts):
        raise ValueError('Invalid application module path')
    return 'app/' + name


def overlay(archive, app, modules, output):
    if sha(archive.read_bytes()) != APP or not modules or len(modules) != len(set(modules)):
        raise ValueError('Expected original R3 archive and distinct changed Python modules')
    changes = {}
    with zipfile.ZipFile(archive) as original:
        for name in sorted(modules):
            key = module_name(name)
            before = original.read(key) if key in original.namelist() else b''
            after = (app / name).read_bytes()
            if not after or after == before:
                raise ValueError('Empty or unchanged module: ' + name)
            ast.parse(after, filename=name)
            old, new = [v.decode('utf-8').splitlines(keepends=True) for v in (before, after)]
            edits = [[a, b, ''.join(new[c:d])] for op, a, b, c, d
                     in SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if op != 'equal']
            changes[name] = dict(before=sha(before) if before else None, after=sha(after), edits=edits)
    output.write_text(json.dumps(changes, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


def updated_files(original, changes):
    if not changes:
        raise ValueError('Missing test candidate')
    result = {}
    for name, delta in changes.items():
        key = module_name(name)
        data = original.read(key) if key in original.namelist() else None
        if delta['before'] != (sha(data) if data is not None else None):
            raise ValueError('Module baseline hash mismatch: ' + name)
        lines = (data or b'').decode('utf-8').splitlines(keepends=True)
        limit = len(lines)
        for start, end, replacement in reversed(delta['edits']):
            if not 0 <= start <= end <= limit:
                raise ValueError('Overlapping delta segments')
            lines[start:end] = [replacement]
            limit = start
        result[key] = ''.join(lines).encode('utf-8')
        if sha(result[key]) != delta['after']:
            raise ValueError('Resulting module hash mismatch: ' + name)
        ast.parse(result[key], filename=name)
    return result


def build(setup, output, delta):
    released = setup.read_bytes()
    if sha(released) != BASE:
        raise ValueError('Unexpected input installer')
    runtime = resource(released, 68890295, b'PK\x03\x04', RUNTIME)
    app = resource(released, 326225, b'PK\x03\x04', APP)
    launcher = HERE / 'Installer.cs'
    if sha(launcher.read_bytes()) != LAUNCHER:
        raise ValueError('Compiler source must be byte-identical to verified R3')
    changes = json.loads(delta.read_text(encoding='utf-8-sig'))
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    new_zip = output / 'app.zip'
    with zipfile.ZipFile(io.BytesIO(app)) as original:
        modified = updated_files(original, changes)
        with zipfile.ZipFile(new_zip, 'w') as candidate:
            for entry in original.infolist():
                content = modified[entry.filename] if entry.filename in modified else original.read(entry.filename)
                candidate.writestr(copy(entry), content, compresslevel=5)
            for key in sorted(modified.keys() - set(original.namelist())):
                info = zipfile.ZipInfo(key, (2026,11,22,0,0,0))
                candidate.writestr(info, modified[key], compress_type=zipfile.ZIP_DEFLATED, compresslevel=5)
        with zipfile.ZipFile(new_zip) as candidate:
            if candidate.testzip() or set(candidate.namelist()) != set(original.namelist()) | set(modified):
                raise ValueError('Archive failed integrity validation')
            for name in candidate.namelist():
                expected = modified[name] if name in modified else original.read(name)
                if expected != candidate.read(name):
                    raise ValueError('Unexpected packaged source change: ' + name)
    (output / 'runtime.zip').write_bytes(runtime)
    packed_app = new_zip.read_bytes()
    check = (RUNTIME + '\n' + sha(packed_app) + '\n').encode('ascii')
    (output / 'checksums').write_bytes(check)
    compiler = Path(os.environ['SYSTEMROOT'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo','/target:exe','/platform:x64','/optimize+',
                    '/reference:System.IO.Compression.dll', '/reference:Microsoft.CSharp.dll',
                    '/resource:runtime.zip,runtime', '/resource:app.zip,app', '/resource:checksums,checksums',
                    '/out:KioskAI-Setup.exe', str(launcher)], cwd=output, check=True)
    candidate = (output/'KioskAI-Setup.exe').read_bytes()
    for blob in (packed_app, runtime, check):
        resource(candidate, len(blob), blob[:32], sha(blob))
    proof = dict(base_setup_sha256=BASE, candidate_setup_sha256=sha(candidate), base_app_sha256=APP,
                 candidate_app_sha256=sha(packed_app), runtime_sha256=RUNTIME,
                 launcher_source_sha256=LAUNCHER, changed_modules={n: d['after'] for n,d in changes.items()})
    (output/'candidate-build.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(json.dumps(proof), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('delta')
    create.add_argument('archive', type=Path)
    create.add_argument('app', type=Path)
    create.add_argument('output', type=Path)
    create.add_argument('modules', nargs='+')
    pack = commands.add_parser('build')
    pack.add_argument('setup', type=Path)
    pack.add_argument('output', type=Path)
    pack.add_argument('delta', type=Path)
    args = parser.parse_args()
    if args.command == 'delta':
        overlay(args.archive, args.app, args.modules, args.output)
    else:
        build(args.setup, args.output, args.delta)
