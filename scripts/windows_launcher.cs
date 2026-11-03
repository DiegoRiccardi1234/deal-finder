using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// The native launcher only resolves its own bundle and preserves all QA args.
static class Launcher {
    static string Quote(string value) {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char ch in value) {
            if (ch == '\\') { slashes++; continue; }
            if (ch == '"') {
                result.Append('\\', slashes * 2 + 1);
                result.Append(ch);
            } else {
                result.Append('\\', slashes);
                result.Append(ch);
            }
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    [STAThread]
    static int Main(string[] args) {
        try {
            string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string runtime = Path.Combine(root, "app", "DealFinder.exe");
            string marker = Path.Combine(root, "app", ".dealfinder-layout.json");
            if (!File.Exists(runtime) || !File.Exists(marker))
                throw new InvalidOperationException("Installazione incompleta: conservare DealFinder.exe insieme ad app e state.");
            var layout = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(marker));
            if (layout == null || !layout.ContainsKey("product") || !layout.ContainsKey("version")
                || !(layout["product"] is string) || (string)layout["product"] != "dealfinder"
                || !(layout["version"] is int) || (int)layout["version"] != 1)
                throw new InvalidOperationException("Marker layout non valido. Ripristinarlo prima di avviare; i dati non sono stati aperti.");
            var quoted = new string[args.Length];
            for (int i = 0; i < args.Length; i++) quoted[i] = Quote(args[i]);
            var start = new ProcessStartInfo(runtime, string.Join(" ", quoted));
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.EnvironmentVariables["DEAL_FINDER_WORKSPACE"] = root;
            start.EnvironmentVariables["DEAL_FINDER_LAUNCHER_PID"] = Process.GetCurrentProcess().Id.ToString();
            using (Process child = Process.Start(start)) {
                child.WaitForExit();
                return child.ExitCode;
            }
        } catch (Exception error) {
            MessageBox.Show(error.Message, "Deal Finder", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
