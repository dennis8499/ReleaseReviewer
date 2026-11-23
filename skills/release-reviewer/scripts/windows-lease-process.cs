using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

public static partial class WindowsLocalLease
{
    private const int SupervisorTimeout = unchecked((int)0xe0470001);
    [StructLayout(LayoutKind.Sequential)]
    private struct JobLimits
    {
        public long PerProcessTime, PerJobTime;
        public uint Flags;
        public UIntPtr MinWorkingSet, MaxWorkingSet;
        public uint ProcessLimit;
        public UIntPtr Affinity;
        public uint Priority, SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedLimits
    {
        public JobLimits Basic;
        public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Accounting
    {
        public long UserTime, KernelTime, PeriodUserTime, PeriodKernelTime;
        public uint PageFaults, Total, Active, Terminated;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct StartupInfo
    {
        public uint Size;
        public IntPtr Reserved, Desktop, Title;
        public uint X, Y, XSize, YSize, XChars, YChars, Fill, Flags;
        public ushort ShowWindow, ReservedSize;
        public IntPtr ReservedBytes, Input, Output, Error;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ExtendedStartupInfo
    {
        public StartupInfo Start;
        public IntPtr Attributes;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessInfo
    {
        public IntPtr Process, Thread;
        public uint ProcessId, ThreadId;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern KernelHandle CreateJobObjectW(IntPtr security, string name);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int SetInformationJobObject(KernelHandle job, int type, ref ExtendedLimits limits, uint size);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int QueryInformationJobObject(KernelHandle job, int type, out Accounting accounting, uint size, IntPtr returned);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int TerminateJobObject(KernelHandle job, uint code);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int InitializeProcThreadAttributeList(IntPtr list, int count, uint flags, ref UIntPtr size);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int UpdateProcThreadAttribute(IntPtr list, uint flags, UIntPtr attribute,
        IntPtr value, UIntPtr size, IntPtr previous, IntPtr returned);

    [DllImport("kernel32.dll", ExactSpelling = true)]
    private static extern void DeleteProcThreadAttributeList(IntPtr list);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern int CreateProcessW(string application, StringBuilder command, IntPtr processSecurity, IntPtr threadSecurity,
        int inherit, uint flags, IntPtr environment, string cwd, ref ExtendedStartupInfo startup, out ProcessInfo process);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern uint ResumeThread(KernelHandle thread);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int TerminateProcess(KernelHandle process, uint exit);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int GetExitCodeProcess(KernelHandle process, out uint code);

    [DllImport("kernel32.dll", ExactSpelling = true)]
    private static extern IntPtr GetCurrentProcess();

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern IntPtr GetStdHandle(int handle);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int DuplicateHandle(IntPtr source, IntPtr handle, IntPtr target, out IntPtr duplicate, uint access, int inherit, uint options);

    private static KernelHandle DuplicateStandardHandle(int standard)
    {
        IntPtr current = GetCurrentProcess(), duplicate;
        if (DuplicateHandle(current, GetStdHandle(standard), current, out duplicate, 0, 1, 2) == 0) throw Win32Error();
        return new KernelHandle(duplicate);
    }

    private static uint Active(KernelHandle job)
    {
        Accounting data;
        if (QueryInformationJobObject(job, 1, out data, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero) == 0)
            throw Win32Error();
        return data.Active;
    }

    private static string Quote(string value)
    {
        if (value.IndexOf('\0') >= 0) throw new LeaseFailure("invalid-request");
        var quoted = new StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value)
        {
            if (character == '\\') { slashes++; continue; }
            if (character == '"') quoted.Append('\\', slashes * 2 + 1);
            else quoted.Append('\\', slashes);
            quoted.Append(character);
            slashes = 0;
        }
        quoted.Append('\\', slashes * 2);
        return quoted.Append('"').ToString();
    }

    private static ProcessInfo Start(KernelHandle job, StringBuilder command, string cwd, bool interactive, IntPtr input)
    {
        using (var output = interactive ? null : DuplicateStandardHandle(-11))
        using (var error = interactive ? null : DuplicateStandardHandle(-12))
        {
            int count = (interactive ? 0 : 1) + (job == null ? 0 : 1);
            UIntPtr size = UIntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero, count, 0, ref size);
            if (size == UIntPtr.Zero) throw Win32Error();
            IntPtr list = Marshal.AllocHGlobal(checked((int)size.ToUInt64()));
            IntPtr values = Marshal.AllocHGlobal(4 * IntPtr.Size);
            bool initialized = false;
            try
            {
                if (InitializeProcThreadAttributeList(list, count, 0, ref size) == 0) throw Win32Error();
                initialized = true;
                if (job != null)
                {
                    // Assign the job before the child can execute.
                    Marshal.WriteIntPtr(values, job.DangerousGetHandle());
                    if (UpdateProcThreadAttribute(list, 0, (UIntPtr)0x2000D, values, (UIntPtr)IntPtr.Size, IntPtr.Zero, IntPtr.Zero) == 0)
                        throw Win32Error();
                }
                var startup = new ExtendedStartupInfo { Attributes = list };
                startup.Start.Size = (uint)Marshal.SizeOf(typeof(ExtendedStartupInfo));
                if (!interactive)
                {
                    startup.Start.Flags = 0x100;
                    startup.Start.Input = input;
                    startup.Start.Output = output.DangerousGetHandle();
                    startup.Start.Error = error.DangerousGetHandle();
                    IntPtr handles = IntPtr.Add(values, IntPtr.Size);
                    Marshal.WriteIntPtr(handles, input);
                    Marshal.WriteIntPtr(handles, IntPtr.Size, startup.Start.Output);
                    Marshal.WriteIntPtr(handles, 2 * IntPtr.Size, startup.Start.Error);
                    if (UpdateProcThreadAttribute(list, 0, (UIntPtr)0x20002, handles, (UIntPtr)(3 * IntPtr.Size), IntPtr.Zero, IntPtr.Zero) == 0)
                        throw Win32Error();
                }
                ProcessInfo process;
                uint flags = 0x00080004U | (interactive ? 0U : 0x08000000U);
                if (CreateProcessW(null, command, IntPtr.Zero, IntPtr.Zero, interactive ? 0 : 1,
                    flags, IntPtr.Zero, cwd, ref startup, out process) == 0) throw Win32Error();
                return process;
            }
            finally
            {
                if (initialized) DeleteProcThreadAttributeList(list);
                Marshal.FreeHGlobal(values);
                Marshal.FreeHGlobal(list);
            }
        }
    }

    public static int StartMonitor(string powershell, string launcher)
    {
        // A grandchild created here stays outside libuv's kill-on-close job.
        // It inherits the redirected pipe handles without opening a console.
        var command = new StringBuilder(Quote(powershell)).Append(" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ")
            .Append(Quote(launcher)).Append(" -Run");
        ProcessInfo created;
        using (var input = DuplicateStandardHandle(-10))
            created = Start(null, command, null, false, input.DangerousGetHandle());
        using (var process = new KernelHandle(created.Process))
        using (var thread = new KernelHandle(created.Thread))
        {
            if (ResumeThread(thread) == UInt32.MaxValue)
            {
                var error = Win32Error();
                TerminateProcess(process, 125);
                throw error;
            }
            uint exit;
            if (WaitForSingleObject(process, UInt32.MaxValue) != 0 || GetExitCodeProcess(process, out exit) == 0)
                throw Win32Error();
            return unchecked((int)exit);
        }
    }

    private static bool IsSharing(Win32Exception error)
    {
        return error.NativeErrorCode == 5 || error.NativeErrorCode == 32 || error.NativeErrorCode == 33;
    }

    private static void Record(Dictionary<string, object> lease, string action, string job)
    {
        var request = new Dictionary<string, object>(lease);
        request["action"] = action;
        request["job"] = job;
        var elapsed = Stopwatch.StartNew();
        for (;;)
        {
            try { Handle(request); return; }
            catch (LeaseFailure error) { if (error.Code != "busy" || elapsed.ElapsedMilliseconds >= 2000) throw; }
            catch (Win32Exception error) { if (!IsSharing(error) || elapsed.ElapsedMilliseconds >= 2000) throw; }
            Thread.Sleep(10);
        }
    }

    private static void Stop(KernelHandle job)
    {
        if (Active(job) == 0) return;
        if (TerminateJobObject(job, 125) == 0) throw Win32Error();
        var elapsed = Stopwatch.StartNew();
        while (Active(job) != 0)
        {
            if (elapsed.ElapsedMilliseconds >= 5000) throw new LeaseFailure("process-tree-unconfirmed");
            Thread.Sleep(10);
        }
    }

    public static int Run(string json, bool interactive)
    {
        var request = Json().Deserialize<Dictionary<string, object>>(json);
        string executable = StringValue(request, "executable");
        if (String.IsNullOrWhiteSpace(executable)) throw new LeaseFailure("invalid-request");
        string cwd = StringValue(request, "cwd");
        if (cwd != null) cwd = Path.GetFullPath(cwd);
        var command = new StringBuilder(Quote(executable));
        foreach (object argument in Items(request, "args"))
        {
            var text = argument as string;
            if (text == null) throw new LeaseFailure("invalid-request");
            command.Append(' ').Append(Quote(text));
        }
        int timeout = Integer(request, "timeoutMs");
        if (timeout <= 0) throw new LeaseFailure("invalid-request");
        var leases = new List<Dictionary<string, object>>();
        foreach (object item in Items(request, "leases"))
        {
            var entry = item as Dictionary<string, object>;
            if (entry == null) throw new LeaseFailure("invalid-request");
            leases.Add(entry);
        }
        if (leases.Count == 0 || leases.Count > 128) throw new LeaseFailure("invalid-request");

        string jobId = Guid.NewGuid().ToString("D");
        // The live handle is the source of completion evidence. Recovery
        // never attempts to infer completion from a missing job name.
        using (var job = CreateJobObjectW(IntPtr.Zero, null))
        using (var input = interactive ? null : new AnonymousPipeServerStream(PipeDirection.Out, HandleInheritability.Inheritable))
        {
            if (job.IsInvalid) throw Win32Error();
            var limits = new ExtendedLimits();
            limits.Basic.Flags = 0x2000; // KILL_ON_JOB_CLOSE, without either breakaway flag.
            if (SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(typeof(ExtendedLimits))) == 0)
                throw Win32Error();
            bool stopped = true;
            var registered = new List<Dictionary<string, object>>();
            try
            {
                var elapsed = Stopwatch.StartNew();
                foreach (var lease in leases)
                {
                    Record(lease, "authorize", jobId);
                    registered.Add(lease);
                }

                if (!interactive && ProcessIdentity(Integer(leases[0], "pid")) != StringValue(leases[0], "birth")) return 125;
                byte[] bytes = Convert.FromBase64String(StringValue(request, "input") ?? "");
                IntPtr inputHandle = interactive ? IntPtr.Zero : input.ClientSafePipeHandle.DangerousGetHandle();
                ProcessInfo created = Start(job, command, cwd, interactive, inputHandle);
                stopped = false;
                uint exit = 125;
                using (var process = new KernelHandle(created.Process))
                using (var thread = new KernelHandle(created.Thread))
                {
                    Task<string> cancel = null;
                    if (!interactive && StringValue(request, "cancel") == "stdin") cancel = Task.Run(() => Console.ReadLine());
                    if (ResumeThread(thread) == UInt32.MaxValue) throw Win32Error();
                    Task feed = null;
                    if (input != null)
                    {
                        input.DisposeLocalCopyOfClientHandle();
                        feed = Task.Run(() => {
                            try { if (bytes.Length > 0) input.Write(bytes, 0, bytes.Length); }
                            catch (IOException) { }
                            finally { input.Dispose(); }
                        });
                    }
                    while (WaitForSingleObject(process, 20) == StillRunning)
                    {
                        if (elapsed.ElapsedMilliseconds >= timeout ||
                            (cancel != null && cancel.IsCompleted) ||
                            (!interactive && ProcessIdentity(Integer(leases[0], "pid")) != StringValue(leases[0], "birth")))
                        {
                            Stop(job);
                            stopped = true;
                            return SupervisorTimeout;
                        }
                    }
                    if (WaitForSingleObject(process, 0) != 0 || GetExitCodeProcess(process, out exit) == 0) throw Win32Error();
                    if (feed != null && !feed.Wait(5000)) throw new LeaseFailure("process-tree-unconfirmed");
                }
                // Let transport helpers settle without releasing the lease on
                // the exit of the top-level command alone.
                var descendants = Stopwatch.StartNew();
                while (Active(job) != 0)
                {
                    if (descendants.ElapsedMilliseconds >= 500 || elapsed.ElapsedMilliseconds >= timeout)
                    {
                        Stop(job);
                        stopped = true;
                        return elapsed.ElapsedMilliseconds >= timeout ? SupervisorTimeout : 125;
                    }
                    Thread.Sleep(10);
                }
                stopped = true;
                return unchecked((int)exit);
            }
            finally
            {
                if (!stopped) Stop(job);
                Exception completionError = null;
                foreach (var lease in registered)
                {
                    try { Record(lease, "complete", jobId); }
                    catch (Exception error) { if (completionError == null) completionError = error; }
                }
                if (completionError != null) throw new LeaseFailure("process-tree-unconfirmed", completionError);
            }
        }
    }
}
