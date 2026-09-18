$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class VulcanFolderPicker {
    [ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IFileDialog {
        [PreserveSig] int Show(IntPtr owner);
        void SetFileTypes(uint count, IntPtr types);
        void SetFileTypeIndex(uint index);
        void GetFileTypeIndex(out uint index);
        void Advise(IntPtr sink, out uint cookie);
        void Unadvise(uint cookie);
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(IShellItem folder);
        void SetFolder(IShellItem folder);
        void GetFolder(out IShellItem folder);
        void GetCurrentSelection(out IShellItem item);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetFileName(out IntPtr name);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void GetResult(out IShellItem item);
    }
    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellItem {
        void BindToHandler(IntPtr context, ref Guid handler, ref Guid iid, out IntPtr result);
        void GetParent(out IShellItem parent);
        void GetDisplayName(uint type, out IntPtr name);
        void GetAttributes(uint mask, out uint attributes);
        void Compare(IShellItem other, uint hint, out int order);
    }
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHCreateItemFromParsingName(string path, IntPtr context, ref Guid iid, out IShellItem item);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();

    public static string Choose(string initial) {
        object instance = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")));
        IFileDialog dialog = (IFileDialog)instance;
        try {
            uint options; dialog.GetOptions(out options);
            dialog.SetOptions(options | 0x20 | 0x40 | 0x800); // PICKFOLDERS, FORCEFILESYSTEM, PATHMUSTEXIST
            dialog.SetTitle("Choose project directory");
            dialog.SetOkButtonLabel("Select folder");
            if (System.IO.Directory.Exists(initial)) {
                Guid iid = new Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE");
                IShellItem folder; SHCreateItemFromParsingName(initial, IntPtr.Zero, ref iid, out folder);
                try { dialog.SetFolder(folder); } finally { Marshal.ReleaseComObject(folder); }
            }
            int status = dialog.Show(GetForegroundWindow());
            if (status == unchecked((int)0x800704C7)) return null;
            Marshal.ThrowExceptionForHR(status);
            IShellItem selected; dialog.GetResult(out selected);
            try {
                IntPtr path; selected.GetDisplayName(0x80058000, out path); // SIGDN_FILESYSPATH
                try { return Marshal.PtrToStringUni(path); } finally { Marshal.FreeCoTaskMem(path); }
            } finally { Marshal.ReleaseComObject(selected); }
        } finally { Marshal.ReleaseComObject(instance); }
    }
}
'@
$selectedDirectory = [VulcanFolderPicker]::Choose($env:VULCAN_PICKER_INITIAL)
@{ directory = $selectedDirectory; cancelled = ($null -eq $selectedDirectory) } | ConvertTo-Json -Compress
