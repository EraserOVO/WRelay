"""Load the small WinForms caption adapter using pywebview's existing .NET runtime."""
from pathlib import Path


def attach_titlebar(native_form, icon_path):
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    clr.AddReference("Microsoft.CSharp")
    from Microsoft.CSharp import CSharpCodeProvider
    from System.CodeDom.Compiler import CompilerParameters

    provider = CSharpCodeProvider()
    parameters = CompilerParameters()
    parameters.GenerateInMemory = True
    for assembly in ("System.dll", "System.Drawing.dll", "System.Windows.Forms.dll"):
        parameters.ReferencedAssemblies.Add(assembly)
    try:
        source = Path(__file__).with_suffix(".cs").read_text(encoding="utf-8")
        result = provider.CompileAssemblyFromSource(parameters, source)
        if result.Errors.HasErrors:
            raise RuntimeError("; ".join(str(error) for error in result.Errors))
        chrome_type = result.CompiledAssembly.GetType("WRelay.Desktop.WindowChrome")
        from System import Activator, Array, Object
        return Activator.CreateInstance(chrome_type, Array[Object]([native_form, icon_path]))
    finally:
        provider.Dispose()
