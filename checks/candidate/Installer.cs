using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

internal static class Installer {
    private static string Digest(Stream stream) {
        using (SHA256 hash = SHA256.Create())
            return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
    }

    private static Stream Resource(string name) {
        return Assembly.GetExecutingAssembly().GetManifestResourceStream(name);
    }

    private static void Unpack(string name, string expected, string target) {
        using (Stream stream = Resource(name)) {
            if (Digest(stream) != expected) throw new InvalidDataException("설치 자료의 해시가 일치하지 않습니다.");
            stream.Position = 0;
            using (var zip = new ZipArchive(stream, ZipArchiveMode.Read)) {
                foreach (var entry in zip.Entries) {
                    string path = Path.GetFullPath(Path.Combine(target, entry.FullName));
                    if (!path.StartsWith(target + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("설치 경로를 확인할 수 없습니다.");
                    Directory.CreateDirectory(Path.GetDirectoryName(path));
                    using (Stream input = entry.Open())
                    using (var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write))
                        input.CopyTo(output);
                }
            }
        }
    }

    private static void Shortcut(string exe, string folder) {
        Type shellType = Type.GetTypeFromProgID("WScript.Shell");
        dynamic shell = Activator.CreateInstance(shellType);
        dynamic link = shell.CreateShortcut(Path.Combine(folder, "KioskAI.lnk"));
        link.TargetPath = exe;
        link.WorkingDirectory = Path.GetDirectoryName(exe);
        link.Description = "KioskAI 로컬 주문 도우미";
        link.Save();
    }

    public static int Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        bool diagnostic = args.Length >= 1 && args[0] == "--verify";
        bool prepareOnly = args.Length == 1 && args[0] == "--prepare-only";
        if (args.Length != 0 && !prepareOnly && !(diagnostic && args.Length <= 2)) return 2;
        try {
            if (!Environment.Is64BitOperatingSystem || !Environment.Is64BitProcess)
                throw new PlatformNotSupportedException("Windows x64 환경이 필요합니다.");
            string root = diagnostic && args.Length == 2 ? Path.GetFullPath(args[1]) :
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "KioskAI");
            string[] hashes;
            using (var manifest = new StreamReader(Resource("checksums"))) 
                hashes = manifest.ReadToEnd().Trim().Split('\n');
            string version = hashes[0].Substring(0, 16) + "-" + hashes[1].Substring(0, 16);
            string dest = Path.Combine(root, "versions", version);
            using (var mutex = new Mutex(false, @"Local\KioskAI-Setup")) {
                try { mutex.WaitOne(); } catch (AbandonedMutexException) {}
                try {
                    if (!Directory.Exists(dest)) {
                        string stage = Path.Combine(root, "versions", "install-" + Guid.NewGuid().ToString("N"));
                        Directory.CreateDirectory(stage);
                        try {
                            Console.WriteLine("KioskAI와 전용 Python 실행 환경을 설치합니다.");
                            Unpack("runtime", hashes[0].Trim(), stage);
                            Unpack("app", hashes[1].Trim(), stage);
                            Directory.Move(stage, dest);
                        } finally {
                            if (Directory.Exists(stage)) Directory.Delete(stage, true);
                        }
                    }
                    if (!diagnostic) {
                        string self = Assembly.GetExecutingAssembly().Location;
                        string launcher = Path.Combine(dest, "KioskAI.exe");
                        if (!File.Exists(launcher)) File.Copy(self, launcher);
                        string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
                        Directory.CreateDirectory(programs);
                        Shortcut(launcher, programs);
                    }
                } finally { mutex.ReleaseMutex(); }
            }
            var start = new ProcessStartInfo(Path.Combine(dest, "python", "python.exe"));
            start.WorkingDirectory = Path.Combine(dest, "app");
            start.Arguments = "-E -s -B -X utf8 distribution\\bootstrap.py" +
                (diagnostic ? " --verify" : prepareOnly ? " --prepare-only" : "");
            start.UseShellExecute = false;
            using (var child = Process.Start(start)) {
                child.WaitForExit();
                if (child.ExitCode != 0 && args.Length == 0 && !Console.IsInputRedirected) {
                    Console.WriteLine("확인하려면 Enter를 누르세요."); Console.ReadLine();
                }
                return child.ExitCode;
            }
        } catch (Exception error) {
            Console.Error.WriteLine("KioskAI 설치 중 문제가 발생했습니다. " + error.Message);
            if (args.Length == 0 && !Console.IsInputRedirected) Console.ReadLine();
            return 1;
        }
    }
}
