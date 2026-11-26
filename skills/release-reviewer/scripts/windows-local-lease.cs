using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

// Windows PowerShell 5.1 loads this alongside the handle-based path checks
// used by offline recovery. The canonical directory remains the v1 mutex.
public static partial class WindowsLocalLease
{
    private const string Schema = "GitLabWorkspaceLease/v1";
    private const string Prefix = @"\\?\";
    private const uint StillRunning = 258;
    private const uint QueryAndSynchronize = 0x101000;

    private sealed class KernelHandle : SafeHandleZeroOrMinusOneIsInvalid
    {
        internal KernelHandle() : base(true) {}
        internal KernelHandle(IntPtr value) : base(true) { SetHandle(value); }
        protected override bool ReleaseHandle() { return CloseHandle(handle) != 0; }
    }

    private sealed class LeaseFailure : Exception
    {
        public readonly string Code;
        public LeaseFailure(string code, Exception cause = null) : base(code, cause) { Code = code; }
    }

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern KernelHandle OpenProcess(uint access, int inherit, int pid);

    [DllImport("kernel32.dll", ExactSpelling = true)]
    private static extern int GetCurrentProcessId();

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int GetProcessTimes(KernelHandle process, out long created, out long exited, out long kernel, out long user);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern uint WaitForSingleObject(KernelHandle handle, uint milliseconds);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int CloseHandle(IntPtr handle);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern int CreateDirectoryW(string path, IntPtr security);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern int RemoveDirectoryW(string path);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern int DeleteFileW(string path);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern int MoveFileExW(string source, string target, uint flags);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string path, uint access, FileShare share,
        IntPtr security, uint disposition, uint flags, IntPtr template);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int GetFileInformationByHandleEx(SafeFileHandle file, int kind, out ulong attributes, uint size);

    private static JavaScriptSerializer Json()
    {
        return new JavaScriptSerializer { MaxJsonLength = 12 * 1024 * 1024, RecursionLimit = 24 };
    }

    private static string StringValue(Dictionary<string, object> data, string name)
    {
        object value;
        return data.TryGetValue(name, out value) ? value as string : null;
    }

    private static int Integer(Dictionary<string, object> data, string name)
    {
        object value;
        if (!data.TryGetValue(name, out value) || !(value is int)) throw new LeaseFailure("invalid-request");
        return (int)value;
    }

    private static Dictionary<string, object> ObjectValue(Dictionary<string, object> data, string name)
    {
        object value;
        return data.TryGetValue(name, out value) ? value as Dictionary<string, object> : null;
    }

    private static object[] Items(Dictionary<string, object> data, string name)
    {
        object value;
        if (!data.TryGetValue(name, out value)) return new object[0];
        var sequence = value as IList;
        if (sequence == null) throw new LeaseFailure("invalid-request");
        var items = new object[sequence.Count];
        sequence.CopyTo(items, 0);
        return items;
    }

    private static bool IsNonce(string value)
    {
        Guid id;
        return Guid.TryParseExact(value, "D", out id) && id != Guid.Empty;
    }

    private static bool IsBirth(string value)
    {
        long ticks;
        return Int64.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out ticks) &&
            ticks > 0 && ticks.ToString(CultureInfo.InvariantCulture) == value;
    }

    private static Win32Exception Win32Error() { return new Win32Exception(Marshal.GetLastWin32Error()); }

    private static string FullPath(string value)
    {
        if (String.IsNullOrEmpty(value) || value.Length < 4 || value[1] != ':' || value[2] != '\\' ||
            value.IndexOf(':', 2) >= 0 ||
            !String.Equals(Path.GetFullPath(value), value, StringComparison.OrdinalIgnoreCase))
            throw new LeaseFailure("unsafe-path");
        return value;
    }

    // Null means Windows confirmed that the positive PID no longer denotes
    // a live process. An inaccessible process cannot authorize recovery.
    private static string ProcessIdentity(int pid)
    {
        if (pid <= 0) throw new LeaseFailure("owner-unconfirmed");
        using (KernelHandle process = OpenProcess(QueryAndSynchronize, 0, pid))
        {
            if (process.IsInvalid)
            {
                int error = Marshal.GetLastWin32Error();
                if (error == 87) return null;
                throw new Win32Exception(error);
            }
            uint wait = WaitForSingleObject(process, 0);
            if (wait == 0) return null;
            if (wait != StillRunning) throw Win32Error();
            long created, exited, kernel, user;
            if (GetProcessTimes(process, out created, out exited, out kernel, out user) == 0) throw Win32Error();
            return created.ToString(CultureInfo.InvariantCulture);
        }
    }

    private static FileStream OpenGuard(string directory)
    {
        var handle = CreateFileW(Prefix + directory + ".v2-guard", 0xC0000000, FileShare.ReadWrite,
            IntPtr.Zero, 4, 0x00200000, IntPtr.Zero);
        try
        {
            if (handle.IsInvalid) throw Win32Error();
            ulong attributes;
            if (GetFileInformationByHandleEx(handle, 9, out attributes, 8) == 0) throw Win32Error();
            if ((attributes & 0x410UL) != 0) throw new LeaseFailure("unsafe-path");
            var stream = new FileStream(handle, FileAccess.ReadWrite);
            try { stream.Lock(0, 1); return stream; }
            catch (IOException error)
            {
                stream.Dispose();
                if ((error.HResult & 0xffff) == 33) throw new LeaseFailure("busy");
                throw;
            }
        }
        catch { handle.Dispose(); throw; }
    }

    private static void WriteOwner(string directory, Dictionary<string, object> data)
    {
        string temporary = directory + "\\.owner-" + Guid.NewGuid().ToString("N") + ".tmp";
        byte[] bytes = new UTF8Encoding(false, true).GetBytes(Json().Serialize(data));
        if (bytes.Length > 4096) throw new LeaseFailure("metadata-unreadable");
        try
        {
            using (var handle = CreateFileW(Prefix + temporary, 0x40000000, FileShare.None, IntPtr.Zero, 1, 0x80, IntPtr.Zero))
            {
                if (handle.IsInvalid) throw Win32Error();
                using (var stream = new FileStream(handle, FileAccess.Write))
                {
                    stream.Write(bytes, 0, bytes.Length);
                    stream.Flush(true);
                }
            }
            if (MoveFileExW(Prefix + temporary, Prefix + directory + "\\owner.json", 9) == 0) throw Win32Error();
        }
        finally { DeleteFileW(Prefix + temporary); }
    }

    private static Dictionary<string, object> ReadOwner(WorkflowLeaseDirectory held)
    {
        try
        {
            byte[] data = WorkflowLeaseDirectory.ReadDocument(held.PathName + "\\owner.json", 4096);
            return Json().Deserialize<Dictionary<string, object>>(new UTF8Encoding(false, true).GetString(data));
        }
        catch (FileNotFoundException) { throw new LeaseFailure("metadata-missing"); }
        catch (ArgumentException) { throw new LeaseFailure("metadata-unreadable"); }
        catch (InvalidOperationException) { throw new LeaseFailure("metadata-unreadable"); }
    }

    private static WorkflowLeaseDirectory OpenOwner(string directory)
    {
        try { return WorkflowLeaseDirectory.Open(directory, false); }
        catch (FileNotFoundException) { return null; }
    }

    private static WorkflowLeaseDirectory OpenForRename(WorkflowLeaseDirectory observed, string nonce)
    {
        // Opening the containing directory for DELETE can block metadata
        // replacement on Windows Server. Close the read handle before asking
        // for DELETE, then check the same directory and owner under the guard.
        observed.Dispose();
        var held = WorkflowLeaseDirectory.Open(observed.PathName, true);
        try
        {
            if (held.Identity != observed.Identity || StringValue(ReadOwner(held), "nonce") != nonce)
                throw new LeaseFailure("owner-changed");
            return held;
        }
        catch { held.Dispose(); throw; }
    }

    private static void Retire(WorkflowLeaseDirectory observed, string nonce)
    {
        string identity = observed.Identity;
        string retired;
        using (var held = OpenForRename(observed, nonce))
            retired = held.Retire(".lease-retired-" + nonce + "-" + Guid.NewGuid().ToString("N"));
        try
        {
            using (var verified = WorkflowLeaseDirectory.Open(retired, false))
            {
                if (verified.Identity != identity || StringValue(ReadOwner(verified), "nonce") != nonce) return;
                // Reclaim only the known marker of the renamed directory.
                if (Directory.GetFileSystemEntries(Prefix + retired).Length != 1) return;
                DeleteFileW(Prefix + retired + "\\owner.json");
            }
            RemoveDirectoryW(Prefix + retired);
        }
        catch (IOException) { }
        catch (Win32Exception) { }
        catch (LeaseFailure) { }
    }

    private static string Publish(string directory, string kind, string nonce, int pid, string identity)
    {
        string staged = Path.Combine(Path.GetDirectoryName(directory), ".lease-stage-" + Guid.NewGuid().ToString("N"));
        if (CreateDirectoryW(Prefix + staged, IntPtr.Zero) == 0) throw Win32Error();
        try
        {
            using (var observed = WorkflowLeaseDirectory.Open(staged, false))
            {
                var info = new Dictionary<string, object> {
                    {"version", 2}, {"birth", identity}, {"directoryId", observed.Identity}, {"jobs", new string[0]},
                    {"settled", false}, {"releaseRequested", false}
                };
                var owner = new Dictionary<string, object> {
                    {"schema", Schema}, {"kind", kind}, {"state", "running"}, {"nonce", nonce}, {"pid", pid},
                    {"createdAt", DateTime.UtcNow.ToString("o")}, {"childPid", null}, {"lifecycle", info}
                };
                WriteOwner(staged, owner);
                try
                {
                    using (var held = OpenForRename(observed, nonce))
                        held.Retire(Path.GetFileName(directory));
                }
                catch (Win32Exception error)
                {
                    if (error.NativeErrorCode == 183 || error.NativeErrorCode == 80) throw new LeaseFailure("busy");
                    throw;
                }
                return observed.Identity;
            }
        }
        finally
        {
            DeleteFileW(Prefix + staged + "\\owner.json");
            RemoveDirectoryW(Prefix + staged);
        }
    }

    private static object Handle(Dictionary<string, object> request)
    {
        string action = StringValue(request, "action");
        int pid = Integer(request, "pid");
        if (action == "probe") return new { birth = ProcessIdentity(pid) };
        string directory = FullPath(StringValue(request, "directory"));
        string kind = StringValue(request, "kind"), nonce = StringValue(request, "nonce");
        string birth = StringValue(request, "birth");
        if (String.IsNullOrEmpty(kind) || kind.Length > 64 || !IsNonce(nonce) || !IsBirth(birth))
            throw new LeaseFailure("invalid-request");
        using (WorkflowLeaseDirectory.Open(Path.GetDirectoryName(directory), false))
        using (OpenGuard(directory))
        {
            using (var held = OpenOwner(directory))
            {
                if (held == null)
                {
                    if (action == "release") return new { released = true };
                    if (action != "claim") throw new LeaseFailure("owner-changed");
                    if (ProcessIdentity(pid) != birth) throw new LeaseFailure("owner-changed");
                    return new { directoryId = Publish(directory, kind, nonce, pid, birth), recovered = false };
                }
                // A retried release can arrive after a successor has published.
                if (action == "release" && StringValue(request, "directoryId") != held.Identity)
                    return new { released = true };
                Dictionary<string, object> owner = ReadOwner(held);
                if (owner == null || StringValue(owner, "schema") != Schema || StringValue(owner, "kind") != kind)
                    throw new LeaseFailure("owner-unconfirmed");
                string previousNonce = StringValue(owner, "nonce");
                int ownerPid = Integer(owner, "pid");
                var info = ObjectValue(owner, "lifecycle");
                if (info == null)
                {
                    if (action != "claim") throw new LeaseFailure("owner-changed");
                    throw new LeaseFailure(ProcessIdentity(ownerPid) == null || StringValue(owner, "state") == "quarantined" ? "owner-unconfirmed" : "busy");
                }
                string ownerBirth = StringValue(info, "birth");
                if (Integer(info, "version") != 2 || StringValue(info, "directoryId") != held.Identity ||
                    !IsNonce(previousNonce) || StringValue(owner, "state") != "running" || !IsBirth(ownerBirth))
                    throw new LeaseFailure("owner-unconfirmed");
                object value;
                if (!info.TryGetValue("settled", out value) || !(value is bool) || !info.ContainsKey("jobs"))
                    throw new LeaseFailure("owner-unconfirmed");
                bool settled = (bool)value;
                if (!info.TryGetValue("releaseRequested", out value) || !(value is bool))
                    throw new LeaseFailure("owner-unconfirmed");
                bool releaseRequested = (bool)value;
                var jobs = new Dictionary<string, Dictionary<string, object>>();
                foreach (object item in Items(info, "jobs"))
                {
                    var job = item as Dictionary<string, object>;
                    string id = job == null ? null : StringValue(job, "id");
                    if (!IsNonce(id) || jobs.ContainsKey(id) || jobs.Count >= 16 ||
                        Integer(job, "pid") <= 0 || !IsBirth(StringValue(job, "birth")))
                        throw new LeaseFailure("owner-unconfirmed");
                    jobs.Add(id, job);
                }
                if (settled && jobs.Count != 0) throw new LeaseFailure("owner-unconfirmed");
                bool same = previousNonce == nonce && ownerPid == pid && ownerBirth == birth;
                if (action == "claim")
                {
                    if (same && !settled && !releaseRequested)
                    {
                        if (ProcessIdentity(pid) != birth || jobs.Count != 0) throw new LeaseFailure("owner-unconfirmed");
                        return new { directoryId = held.Identity, recovered = false };
                    }
                    string live = settled ? null : ProcessIdentity(ownerPid);
                    if (!settled && live == ownerBirth) throw new LeaseFailure("busy");
                    if (jobs.Count != 0)
                    {
                        foreach (var job in jobs.Values)
                            if (ProcessIdentity(Integer(job, "pid")) != StringValue(job, "birth"))
                                throw new LeaseFailure("process-tree-unconfirmed");
                        throw new LeaseFailure("busy");
                    }
                    if (ProcessIdentity(pid) != birth) throw new LeaseFailure("owner-changed");
                    Retire(held, previousNonce);
                    return new { directoryId = Publish(directory, kind, nonce, pid, birth), recovered = true };
                }
                if (!same || StringValue(request, "directoryId") != held.Identity) throw new LeaseFailure("owner-changed");
                if (action == "release")
                {
                    if (jobs.Count != 0)
                    {
                        if (!releaseRequested) { info["releaseRequested"] = true; WriteOwner(directory, owner); }
                        throw new LeaseFailure("process-tree-unconfirmed");
                    }
                    if (!settled) { info["settled"] = true; WriteOwner(directory, owner); }
                    Retire(held, nonce);
                    return new { released = true };
                }
                if (action == "status") return new { active = jobs.Count, settled = settled };
                if (settled) throw new LeaseFailure("owner-changed");
                string jobId = StringValue(request, "job");
                if (!IsNonce(jobId)) throw new LeaseFailure("invalid-request");
                int supervisor = GetCurrentProcessId();
                if (action == "authorize")
                {
                    if (releaseRequested || ProcessIdentity(pid) != birth) throw new LeaseFailure("owner-changed");
                    if (!jobs.ContainsKey(jobId))
                    {
                        if (jobs.Count >= 16) throw new LeaseFailure("busy");
                        jobs.Add(jobId, new Dictionary<string, object> {
                            {"id", jobId}, {"pid", supervisor}, {"birth", ProcessIdentity(supervisor)}
                        });
                    }
                }
                else if (action == "complete")
                {
                    Dictionary<string, object> recorded;
                    if (jobs.TryGetValue(jobId, out recorded) &&
                        (Integer(recorded, "pid") != supervisor || StringValue(recorded, "birth") != ProcessIdentity(supervisor)))
                        throw new LeaseFailure("owner-changed");
                    jobs.Remove(jobId);
                    if (releaseRequested && jobs.Count == 0) info["settled"] = true;
                }
                else throw new LeaseFailure("invalid-request");
                var remaining = new Dictionary<string, object>[jobs.Count];
                jobs.Values.CopyTo(remaining, 0);
                info["jobs"] = remaining;
                WriteOwner(directory, owner);
                return new { recorded = true };
            }
        }
    }

    public static void Serve()
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        Console.InputEncoding = new UTF8Encoding(false);
        Console.WriteLine("GitLabWorkspaceWindowsLease/v2");
        string line;
        while ((line = Console.ReadLine()) != null)
        {
            object result;
            try
            {
                var request = Json().Deserialize<Dictionary<string, object>>(line);
                string action = StringValue(request, "action");
                if (action != "claim" && action != "release" && action != "probe" && action != "status")
                    throw new LeaseFailure("invalid-request");
                result = new { ok = true, data = Handle(request) };
            }
            catch (LeaseFailure error) { result = new { ok = false, code = error.Code }; }
            catch (Win32Exception error) { result = new { ok = false, code = "windows-io", nativeCode = error.NativeErrorCode }; }
            catch (Exception) { result = new { ok = false, code = "metadata-unreadable" }; }
            Console.WriteLine(Json().Serialize(result));
        }
    }
}
