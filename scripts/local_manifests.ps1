# P4-B2 第三份校驗清單（使用者本機 Windows；docs/P4-PREVIEW.md §32；裁定 #76 Q5「第三份先放使用者本機」）。
# 下載 GitHub Release 上所有 snap-YYYYMMDD 的 manifest-<T>.json 到本機資料夾（全歷史、不刪），並與上次存下的版本比對：
#   - 新的 T：存檔。
#   - 同一個 T 內容沒變：略過。
#   - 同一個 T 內容變了：舊檔改名 manifest-<T>.<舊檔 sha256 前 12 碼>.json 保留、新檔存入，並列在報告；
#     T 早於今天 2 天以上仍變動標「異常」（正常情況下快照只在 T 之後一天內更新）。
# 只讀公開 API、免 token、不上傳任何東西。相容 Windows PowerShell 5.1 與 PowerShell 7。
# 用法（PowerShell）：
#   powershell -ExecutionPolicy Bypass -File .\local_manifests.ps1                       # 預設存到 %USERPROFILE%\iching-backup\manifests
#   powershell -ExecutionPolicy Bypass -File .\local_manifests.ps1 -Dest D:\backup\iching
# 檔案存成 UTF-8 with BOM：Windows PowerShell 5.1 讀無 BOM 的 .ps1 會當成系統碼頁（cp950），中文字串會壞。
# 一行安裝（下載到暫存、以子行程執行，結束時不會關掉你的視窗）：
#   iwr https://raw.githubusercontent.com/shihpc/taiwan-stock-iching/main/scripts/local_manifests.ps1 -OutFile $env:TEMP\lm.ps1 -UseBasicParsing; powershell -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\lm.ps1 -Install
# 結束碼：0 正常；1 有下載失敗或「異常」；2 參數錯。
param(
  [string]$Dest = (Join-Path $env:USERPROFILE "iching-backup\manifests"),
  [string]$Repo = "shihpc/taiwan-stock-iching",
  [switch]$Install
)
$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}
if ($Repo -notmatch '^[\w.-]+/[\w.-]+$') { Write-Output "!! Repo 參數錯：$Repo"; exit 2 }
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
# -Install：把本腳本複製到 %USERPROFILE%\iching-backup\local_manifests.ps1，並以 schtasks 建立「目前使用者」每日 19:00 的排程
# （同名排程存在就覆蓋；電腦當時沒開機就等下次）。之後照常往下跑一次。
if ($Install) {
  $home2 = Join-Path $env:USERPROFILE "iching-backup"
  New-Item -ItemType Directory -Force -Path $home2 | Out-Null
  $copy = Join-Path $home2 "local_manifests.ps1"
  if ($PSCommandPath -and ($PSCommandPath -ne $copy)) { Copy-Item -LiteralPath $PSCommandPath -Destination $copy -Force }
  $tr = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$copy`" -Dest `"$Dest`""
  schtasks /Create /SC DAILY /ST 19:00 /TN "iching-manifests" /TR $tr /F | Out-Null
  if ($LASTEXITCODE -eq 0) { Write-Output "已建立每日 19:00 排程 iching-manifests（執行 $copy）" }
  else { Write-Output "!! 建立排程失敗（schtasks 結束碼 $LASTEXITCODE），本次仍照常下載" }
}
$headers = @{ "User-Agent" = "iching-local-manifests"; "Accept" = "application/vnd.github+json" }
$today = [DateTime]::UtcNow.AddHours(8).Date
$errors = New-Object System.Collections.Generic.List[string]
$notes = New-Object System.Collections.Generic.List[string]
$new = 0; $same = 0

function Get-Sha256Hex([byte[]]$bytes) {
  $h = [System.Security.Cryptography.SHA256]::Create()
  try { return ([System.BitConverter]::ToString($h.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() } finally { $h.Dispose() }
}

$releases = @()
$page = 1
try {
  while ($true) {
    $r = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases?per_page=100&page=$page" -Headers $headers -TimeoutSec 60
    $r = @($r)
    $releases += $r
    if ($r.Count -lt 100) { break }
    $page++
  }
} catch {
  Write-Output "!! 列 Release 失敗：$($_.Exception.Message)"
  exit 1
}

foreach ($rel in $releases) {
  $tag = [string]$rel.tag_name
  if ($tag -notmatch '^snap-(\d{4})(\d{2})(\d{2})$') { continue }
  $T = "$($Matches[1])-$($Matches[2])-$($Matches[3])"
  $name = "manifest-$T.json"
  $asset = @($rel.assets) | Where-Object { $_.name -eq $name } | Select-Object -First 1
  if (-not $asset) { $errors.Add("$tag 缺 $name"); continue }
  try {
    # 存成暫存檔再讀位元組：避免 PowerShell 5.1 依回應標頭猜編碼把內容當字串解碼（會改到位元組、sha 對不上）
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
      Invoke-WebRequest -Uri $asset.browser_download_url -Headers @{ "User-Agent" = "iching-local-manifests" } -UseBasicParsing -TimeoutSec 120 -OutFile $tmp
      $bytes = [System.IO.File]::ReadAllBytes($tmp)
    } finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    $obj = [System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
    if ($obj.date -ne $T) { throw "清單 date 與 tag 不符" }
  } catch {
    $errors.Add("$T 清單下載或解析失敗：$($_.Exception.Message)"); continue
  }
  $path = Join-Path $Dest $name
  if (Test-Path -LiteralPath $path) {
    $old = [System.IO.File]::ReadAllBytes($path)
    $oldSha = Get-Sha256Hex $old
    if ($oldSha -eq (Get-Sha256Hex $bytes)) { $same++; continue }
    $oldContent = $null
    try { $oldContent = ([System.Text.Encoding]::UTF8.GetString($old) | ConvertFrom-Json).content_sha256 } catch {}
    if ($oldContent -and $oldContent -eq $obj.content_sha256) {
      [System.IO.File]::WriteAllBytes($path, $bytes)   # 只有 commit／generated_at 不同：以新版為準
      $same++; continue
    }
    $bak = Join-Path $Dest ("manifest-$T." + $oldSha.Substring(0, 12) + ".json")
    Move-Item -LiteralPath $path -Destination $bak -Force
    [System.IO.File]::WriteAllBytes($path, $bytes)
    $age = ($today - [DateTime]::ParseExact($T, "yyyy-MM-dd", $null)).Days
    $kind = if ($age -ge 2) { "異常" } else { "更新" }
    $notes.Add("${kind}：$T 清單內容變動（舊檔留存為 $(Split-Path $bak -Leaf)，T 距今 $age 天）")
    if ($kind -eq "異常") { $errors.Add("$T 清單在 $age 天後仍被改動") }
  } else {
    [System.IO.File]::WriteAllBytes($path, $bytes)
    $new++
  }
}

Write-Output "== local_manifests $(Get-Date -Format s) dest=$Dest"
Write-Output "新存 $new；未變 $same；資料夾共 $(@(Get-ChildItem -LiteralPath $Dest -Filter 'manifest-*.json').Count) 檔"
foreach ($n in $notes) { Write-Output "※ $n" }
foreach ($e in $errors) { Write-Output "!! $e" }
if ($errors.Count -gt 0) { Write-Output "結果：有問題"; exit 1 } else { Write-Output "結果：正常"; exit 0 }
