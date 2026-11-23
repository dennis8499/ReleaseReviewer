using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

// Loaded by Windows PowerShell. Hold the source and its ancestors open until
// the rename commits so the validated object cannot be substituted by path.
public sealed class WorkflowLeaseDirectory : IDisposable
{
    private const uint ReadAttributes = 0x80;
    private const uint DeleteAccess = 0x10000;
    private const uint GenericRead = 0x80000000;
    private const uint OpenExisting = 3;
    private const uint BackupSemantics = 0x02000000;
    private const uint OpenReparsePoint = 0x00200000;
    private const uint DirectoryAttribute = 0x10;
    private const uint ReparseAttribute = 0x400;
    private const int FileRenameInfo = 3;
    private const int FileAttributeTagInfo = 9;
    private const int FileIdInfo = 18;
    private const string ExtendedPathPrefix = @"\\?\";

    [StructLayout(LayoutKind.Sequential)]
    private struct AttributesInfo
    {
        public uint Attributes;
        public uint Tag;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IdentityInfo
    {
        public ulong VolumeSerialNumber;
        public Guid FileId;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct RenameInfo
    {
        public uint Flags;
        public IntPtr RootDirectory;
        public uint FileNameLength;
        public ushort FirstCharacter;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string path, uint desiredAccess, FileShare shareMode,
        IntPtr securityAttributes, uint creationDisposition, uint flags, IntPtr template);

    [DllImport("kernel32.dll", EntryPoint = "GetFileInformationByHandleEx", ExactSpelling = true, SetLastError = true)]
    private static extern int GetAttributes(SafeFileHandle handle, int infoClass, out AttributesInfo info, uint size);

    [DllImport("kernel32.dll", EntryPoint = "GetFileInformationByHandleEx", ExactSpelling = true, SetLastError = true)]
    private static extern int GetId(SafeFileHandle handle, int infoClass, out IdentityInfo info, uint size);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, [Out] char[] path, uint length, uint flags);

    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    private static extern int SetFileInformationByHandle(SafeFileHandle handle, int infoClass, IntPtr info, uint size);

    private readonly List<SafeFileHandle> handles = new List<SafeFileHandle>();
    public string PathName { get; private set; }
    public string Identity { get; private set; }

    public static WorkflowLeaseDirectory Open(string path, bool forRetirement)
    {
        var directory = new WorkflowLeaseDirectory { PathName = path };
        try
        {
            var ancestors = new Stack<string>();
            for (string parent = path; parent != null; parent = Path.GetDirectoryName(parent))
                ancestors.Push(parent);

            while (ancestors.Count > 0)
            {
                string component = ancestors.Pop();
                uint access = ReadAttributes;
                if (ancestors.Count == 0 && forRetirement) access |= DeleteAccess;
                SafeFileHandle handle = OpenChecked(component, access, FileShare.Read | FileShare.Write, true);
                directory.handles.Add(handle);
            }
            IdentityInfo id;
            if (GetId(directory.Handle, FileIdInfo, out id, (uint)Marshal.SizeOf(typeof(IdentityInfo))) == 0)
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if (id.FileId == Guid.Empty) throw new IOException("The directory has no verifiable file ID.");
            directory.Identity = id.VolumeSerialNumber.ToString("X16") + ":" + id.FileId.ToString("N");
            return directory;
        }
        catch
        {
            directory.Dispose();
            throw;
        }
    }

    public static byte[] ReadDocument(string path, int maxBytes)
    {
        return ReadDocument(path, maxBytes, false);
    }

    public static byte[] ReadDocument(string path, int maxBytes, bool allowEmpty)
    {
        using (SafeFileHandle handle = OpenChecked(path, GenericRead, FileShare.Read, false))
        using (var stream = new FileStream(handle, FileAccess.Read))
        {
            long length = stream.Length;
            if ((!allowEmpty && length < 1) || length > maxBytes) throw new IOException("The metadata size is invalid.");
            var bytes = new byte[(int)length];
            for (int position = 0; position < bytes.Length;)
            {
                int read = stream.Read(bytes, position, bytes.Length - position);
                if (read == 0) throw new EndOfStreamException();
                position += read;
            }
            return bytes;
        }
    }

    public string Retire(string siblingName)
    {
        if (handles.Count < 2 || String.IsNullOrEmpty(siblingName) || siblingName.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
            siblingName == "." || siblingName == "..")
            throw new ArgumentException("A sibling name is required for retirement.");

        string destination = Path.Combine(Path.GetDirectoryName(PathName), siblingName);
        byte[] name = Encoding.Unicode.GetBytes(ExtendedPathPrefix + destination + '\0');
        Type layout = typeof(RenameInfo);
        int nameOffset = Marshal.OffsetOf(layout, "FirstCharacter").ToInt32();
        int size = Math.Max(nameOffset + name.Length, Marshal.SizeOf(layout));
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(new RenameInfo {
                // Zero flags means fail if the destination already exists.
                FileNameLength = (uint)(name.Length - 2)
            }, buffer, false);
            Marshal.Copy(name, 0, IntPtr.Add(buffer, nameOffset), name.Length);
            if (SetFileInformationByHandle(Handle, FileRenameInfo, buffer, (uint)size) == 0)
                throw new Win32Exception(Marshal.GetLastWin32Error());
            return destination;
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    private SafeFileHandle Handle { get { return handles[handles.Count - 1]; } }

    private static SafeFileHandle OpenChecked(string path, uint access, FileShare share, bool expectDirectory)
    {
        SafeFileHandle handle = CreateFileW(ExtendedPathPrefix + path, access, share, IntPtr.Zero, OpenExisting,
            BackupSemantics | OpenReparsePoint, IntPtr.Zero);
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            if (error == 2 || error == 3) throw new FileNotFoundException("The path does not exist.", path);
            throw new Win32Exception(error);
        }
        try
        {
            AttributesInfo info;
            if (GetAttributes(handle, FileAttributeTagInfo, out info, (uint)Marshal.SizeOf(typeof(AttributesInfo))) == 0)
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if ((info.Attributes & ReparseAttribute) != 0 || ((info.Attributes & DirectoryAttribute) != 0) != expectDirectory)
                throw new IOException("The path is redirected or is not the expected file type.");

            var finalPath = new char[32768];
            uint length = GetFinalPathNameByHandleW(handle, finalPath, (uint)finalPath.Length, 0);
            if (length == 0) throw new Win32Exception(Marshal.GetLastWin32Error());
            if (length >= finalPath.Length || !String.Equals(new string(finalPath, 0, (int)length),
                ExtendedPathPrefix + path, StringComparison.OrdinalIgnoreCase))
                throw new IOException("The path does not resolve to the specified local file.");
            return handle;
        }
        catch
        {
            handle.Dispose();
            throw;
        }
    }

    public void Dispose()
    {
        for (int i = handles.Count - 1; i >= 0; i--) handles[i].Dispose();
        handles.Clear();
    }
}
