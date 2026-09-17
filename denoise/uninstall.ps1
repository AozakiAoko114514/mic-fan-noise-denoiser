# uninstall.ps1 —— 一键还原：删除本项目产生的一切，不动系统任何设置
param(
    [switch]$All      # 连本文件夹里的脚本和文档也一起删掉
)

$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot

Write-Output "=== mic-denoise 卸载 ==="

# 1) 停掉正在运行的降噪进程
$killed = 0
Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.CommandLine -and $_.CommandLine -match 'live\.py') {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $killed++ } catch {}
    }
}
if ($killed -eq 0) {
    Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$root*" } | ForEach-Object {
        try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; $killed++ } catch {}
    }
}
Write-Output "  已停止降噪进程: $killed 个"

# 2) 删除项目产生的文件
$targets = @('lib', 'wheels', 'tmp', '__pycache__', 'tests\__pycache__',
             'config.json', 'ab_1_raw.wav', 'ab_2_denoised.wav',
             'env_probe.py', 'tmpprobe.py')
foreach ($t in $targets) {
    $p = Join-Path $root $t
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
        Write-Output "  已删除: $t"
    }
}

# 3) 未做过的系统级改动（此处仅确认，不做任何操作）
Write-Output ""
Write-Output "以下内容从未被本程序修改，因此无需还原："
Write-Output "  - 注册表（未写入任何键值）"
Write-Output "  - 音频驱动（未安装 VB-Cable 等虚拟声卡）"
Write-Output "  - 系统音频设置（默认设备、音频增强均未改动）"
Write-Output "  - 系统 Python（依赖装在项目内 lib\，已随上面删除）"
Write-Output ""
Write-Output "Voicemeeter 侧做过的改动（为完成配置所必需，可完整还原）："
Write-Output "  - 虚拟输入条打开 B1 总线（把送进来的音频变成虚拟麦克风）"
Write-Output "  - 关闭所有 Strip 的 A1~A5 硬件监听（防止麦克风被播到音箱啸叫）"
Write-Output "  - 静音硬件输入条"
Write-Output "  还原方法（需 Voicemeeter 正在运行，且 lib\ 还没被删）："
Write-Output "      python tests\vm_restore.py"
Write-Output "  或者手动：在 Voicemeeter 界面上点回来即可；也可直接关闭 Voicemeeter。"
Write-Output ""
Write-Output "提醒：请在会用麦克风的应用里把输入设备从 Voicemeeter 通道改回 '麦克风阵列'。"

if ($All) {
    Write-Output ""
    Write-Output "正在删除整个项目文件夹: $root"
    Set-Location $env:TEMP
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    Write-Output "  完成（若文件夹仍存在，说明有文件被占用，关闭相关程序后重试）"
} else {
    Write-Output ""
    Write-Output "脚本与文档保留。要连文件夹一起删除，请加 -All 参数。"
}
