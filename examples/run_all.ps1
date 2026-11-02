# 一键重放（Windows PowerShell）
#   .\examples\run_all.ps1            # 离线策略模型，无需网络与 key
#   .\examples\run_all.ps1 -Model real  # 真实模型（需先在 .env 配置 key）
param(
    [string]$Model = "offline"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host "== 1/3 运行单元与场景测试 ==" -ForegroundColor Cyan
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { Write-Host "测试失败" -ForegroundColor Red; exit $LASTEXITCODE }

Write-Host "== 2/3 重放全部用例并刷新 examples 产物 ==" -ForegroundColor Cyan
python -m src.cli replay --model $Model
if ($LASTEXITCODE -ne 0) { Write-Host "用例断言失败" -ForegroundColor Red; exit $LASTEXITCODE }

Write-Host "== 3/3 完成：查看 examples/cases/E02_slot_conflict/report.md ==" -ForegroundColor Green
