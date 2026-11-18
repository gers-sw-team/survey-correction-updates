[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string[]]$UpdaterExe,
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [string]$OutputRoot,
    [string]$NotesFile,
    [string]$MetadataFile,
    [switch]$ExpectNoUpdates,
    [switch]$KeepServer
)
$ErrorActionPreference='Stop'
$taskRepo=[System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if(-not $OutputRoot){$OutputRoot=Join-Path $taskRepo ('.lab\'+[DateTime]::Now.ToString('yyyyMMdd-HHmmss-fff'))}
$taskRoot=[System.IO.Path]::GetFullPath($OutputRoot)
if(Test-Path -LiteralPath $taskRoot){throw 'The lab output folder must be new; no existing files will be overwritten.'}
$taskSources=@($UpdaterExe|ForEach-Object{Get-Item -LiteralPath $_})
$taskSourceHashes=@($taskSources|ForEach-Object{[pscustomobject]@{Path=$_.FullName;Sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}})
[void](New-Item -ItemType Directory -Path $taskRoot)
$taskSite=Join-Path $taskRoot 'site'
[void](New-Item -ItemType Directory -Path $taskSite)
$taskStatePath=Join-Path $taskRoot 'server.json'
$taskServerScript=Join-Path $PSScriptRoot 'serve_site_lab.py'
$taskArguments=@(('"'+$taskServerScript+'"'),'--root',('"'+$taskSite+'"'),'--state-file',('"'+$taskStatePath+'"'),'--requests-log',('"'+(Join-Path $taskRoot 'requests.jsonl')+'"'))
$taskServerProcess=Start-Process -FilePath $PythonExe -ArgumentList $taskArguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRoot 'server.stdout.log') -RedirectStandardError (Join-Path $taskRoot 'server.stderr.log')
try{
    for($taskAttempt=0;$taskAttempt -lt 40 -and -not(Test-Path -LiteralPath $taskStatePath);$taskAttempt++){Start-Sleep -Milliseconds 150}
    if(-not(Test-Path -LiteralPath $taskStatePath)){throw 'The loopback metadata server did not start.'}
    $taskServer=Get-Content -LiteralPath $taskStatePath -Raw|ConvertFrom-Json
    if($taskServer.pid -ne $taskServerProcess.Id){throw 'The server PID does not match the lab process.'}
    $taskBase='http://127.0.0.1:'+$taskServer.port+$taskServer.prefix
    $taskClients=@()
    for($taskIndex=0;$taskIndex -lt $taskSources.Count;$taskIndex++){
        $taskDirectory=Join-Path $taskRoot ('clients\'+($taskIndex+1))
        [void](New-Item -ItemType Directory -Path $taskDirectory)
        [void](New-Item -ItemType Directory -Path (Join-Path $taskDirectory 'cache'))
        Copy-Item -LiteralPath $taskSources[$taskIndex].FullName -Destination (Join-Path $taskDirectory 'updater.exe')
        $taskIni=@('[General]',('AppDir='+$taskDirectory+'\'),('ApplicationName=SC Pages Generator Lab '+($taskIndex+1)),
            'CompanyName=SC Pages Generator Lab','ApplicationVersion=0.0.0','DefaultCommandLine=/checknow -minuseractions',
            'CheckFrequency=1',('DownloadsFolder='+(Join-Path $taskDirectory 'cache')),
            'Flags=NoDisableAutoCheck|InstallSummaryAlways|GitHubUpdates',('ID={'+[guid]::NewGuid().ToString().ToUpperInvariant()+'}'),
            ('URL='+$taskBase+'/updates.json'),'') -join "`r`n"
        [System.IO.File]::WriteAllText((Join-Path $taskDirectory 'updater.ini'),$taskIni,[System.Text.UTF8Encoding]::new($false))
        $taskClients+=$taskDirectory
    }
    if(-not $NotesFile){$NotesFile=Join-Path $taskRepo 'tests\fixtures\russian-notes.txt'}
    $taskBody=[System.IO.File]::ReadAllText($NotesFile,[System.Text.Encoding]::UTF8)
    if($MetadataFile){$taskMetadata=Get-Content -LiteralPath $MetadataFile -Raw -Encoding UTF8|ConvertFrom-Json}
    else{$taskMetadata=[pscustomobject]@{RegistryKey=('HKCU\Software\SC Pages Generator Lab\'+[guid]::NewGuid().ToString()+'\Version');Version='99.0.1';Flags='Sys64';ServerFileName='never-install.exe'}}
    if($taskMetadata.Version -notmatch '^\d+\.\d+\.\d+$'){throw 'The metadata version must use major.minor.patch format.'}
    $taskInstallerName=if($taskMetadata.ServerFileName){[string]$taskMetadata.ServerFileName}else{'never-install.exe'}
    $taskMetadataPath=Join-Path $taskSite 'advinst_update.json'
    [System.IO.File]::WriteAllText($taskMetadataPath,($taskMetadata|ConvertTo-Json),[System.Text.UTF8Encoding]::new($false))
    $taskFixture=@(@{id=1;node_id='SCPAGESGENERATORLAB';name='SC Pages Generator Lab';tag_name=$taskMetadata.Version;draft=$false;prerelease=$false;
        published_at='2026-10-29T00:00:00Z';body=$taskBody;assets=@(
            @{id=10;name=$taskInstallerName;state='uploaded';size=1;browser_download_url=('http://127.0.0.1:'+$taskServer.port+'/blocked/never-install.exe')}
        )})
    $taskFixture[0].assets+=@{id=11;name='advinst_update.json';state='uploaded';size=(Get-Item -LiteralPath $taskMetadataPath).Length;browser_download_url=($taskBase+'/advinst_update.json')}
    $taskFixturePath=Join-Path $taskRoot 'source-releases.json'
    [System.IO.File]::WriteAllText($taskFixturePath,(ConvertTo-Json -InputObject $taskFixture -Depth 10),[System.Text.UTF8Encoding]::new($false))
    & $PythonExe (Join-Path $taskRepo 'scripts\build_updates.py') --input $taskFixturePath --output-dir $taskSite
    if($LASTEXITCODE -ne 0){throw 'The production generator failed on the Russian lab fixture.'}
    $taskFeed=Join-Path $taskSite 'updates.json'
    $taskDigest=(Get-FileHash -LiteralPath $taskFeed -Algorithm SHA256).Hash.ToLowerInvariant()
    & $PythonExe (Join-Path $taskRepo 'scripts\verify_updates.py') --url ($taskBase+'/updates.json') --sha256 $taskDigest
    if($LASTEXITCODE -ne 0){throw 'The HTTP file differs from the generated artifact.'}
    $taskExpected=@{FeatureHtml=@();EnhancementHtml=@();BugFixHtml=@()}
    $taskSection=$null
    foreach($taskLine in ($taskBody -split '\r?\n')){
        switch -Exact ($taskLine.Trim()){'## Features'{$taskSection='FeatureHtml'}'## Enhancements'{$taskSection='EnhancementHtml'}'## Bug Fixes'{$taskSection='BugFixHtml'}}
        if($taskSection -and $taskLine.StartsWith('- ')){$taskExpected[$taskSection]+= $taskLine.Substring(2)}
    }
    $taskResults=@()
    foreach($taskDirectory in $taskClients){
        $taskDump=Join-Path $taskDirectory 'detected.aiu'
        $taskProcess=Start-Process -FilePath (Join-Path $taskDirectory 'updater.exe') -ArgumentList @('/justcheck','-dumpdetected',('"'+$taskDump+'"')) -WorkingDirectory $taskDirectory -WindowStyle Hidden -PassThru
        if(-not $taskProcess.WaitForExit(30000)){$taskProcess.Kill();throw ('The copied updater timed out: '+$taskDirectory)}
        $taskProcess.Refresh()
        if($ExpectNoUpdates){
            if($taskProcess.ExitCode -ne -536870895){throw ('Expected no updates, received '+$taskProcess.ExitCode)}
            $taskResults+=[pscustomobject]@{Client=$taskDirectory;Source=$taskSourceHashes[$taskResults.Count].Path;ExitCode=$taskProcess.ExitCode;CorrectlyOffersNoUpdate=$true}
            continue
        }
        if($taskProcess.ExitCode -ne 0){throw ('The copied updater returned '+$taskProcess.ExitCode)}
        $taskBytes=[System.IO.File]::ReadAllBytes($taskDump)
        $taskEncoding='utf-8'
        if($taskBytes.Length -gt 1 -and $taskBytes[0]-eq255 -and $taskBytes[1]-eq254){$taskEncoding='utf-16le';$taskText=[System.Text.Encoding]::Unicode.GetString($taskBytes)}
        else{try{$taskText=[System.Text.UTF8Encoding]::new($false,$true).GetString($taskBytes)}catch{$taskEncoding='windows-1251';$taskText=[System.Text.Encoding]::GetEncoding(1251).GetString($taskBytes)}}
        $taskValues=@{}
        foreach($taskLine in ($taskText -split '\r?\n')){if($taskLine -match '^((?:FeatureHtml|EnhancementHtml|BugFixHtml)\d*|RegistryKey|Version|Flags|SHA256|ServerFileName)=(.*)$'){$taskValues[$Matches[1]]=$Matches[2]}}
        $taskDetection=@{RegistryKey=$taskMetadata.RegistryKey;Version=('>= '+$taskMetadata.Version);ServerFileName=$taskInstallerName}
        if($taskMetadata.SHA256){$taskDetection.SHA256=$taskMetadata.SHA256}
        foreach($taskKey in $taskDetection.Keys){if($taskValues[$taskKey] -cne $taskDetection[$taskKey]){throw ('Updater metadata did not match: '+$taskKey)}}
        if('Sys64' -in ($taskMetadata.Flags -split '\|') -and 'Sys64' -notin ($taskValues.Flags -split '\|')){throw 'The native updater did not retain the Sys64 detection flag.'}
        $taskMatched=0
        foreach($taskField in @('FeatureHtml','EnhancementHtml','BugFixHtml')){
            for($taskNumber=0;$taskNumber -lt $taskExpected[$taskField].Count;$taskNumber++){
                $taskKey=$taskField+$(if($taskNumber -gt 0){[string]$taskNumber}else{''})
                if(-not $taskValues.ContainsKey($taskKey) -or $taskValues[$taskKey] -cne $taskExpected[$taskField][$taskNumber]){throw ('Russian text did not match exactly: '+$taskDirectory+' / '+$taskKey)}
                $taskMatched++
            }
        }
        $taskResults+=[pscustomobject]@{Client=$taskDirectory;Source=$taskSourceHashes[$taskResults.Count].Path;SourceSha256=$taskSourceHashes[$taskResults.Count].Sha256;
            ExitCode=$taskProcess.ExitCode;DumpEncoding=$taskEncoding;MatchedRussianBullets=$taskMatched;AllThreeSectionsMatched=$true;DetectionMetadataMatched=$true;Dump=$taskDump}
    }
    $taskBlockProbe=@'
import sys
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener
opener = build_opener(ProxyHandler({}))
try:
    with opener.open(sys.argv[1], timeout=3) as response:
        print(response.status)
except HTTPError as error:
    print(error.code)
'@
    $taskBlockCode=& $PythonExe -c $taskBlockProbe ('http://127.0.0.1:'+$taskServer.port+'/blocked/never-install.exe')
    if($LASTEXITCODE -ne 0){throw 'The blocked installer probe failed.'}
    if([string]$taskBlockCode -ne '403'){throw 'The lab installer route was not blocked.'}
    foreach($taskSource in $taskSourceHashes){if((Get-FileHash -LiteralPath $taskSource.Path -Algorithm SHA256).Hash -ne $taskSource.Sha256){throw 'An original updater file changed.'}}
    $taskReport=[pscustomobject]@{Directory=$taskRoot;FeedUrl=($taskBase+'/updates.json');FeedSha256=$taskDigest;AsciiOnly=(@([System.IO.File]::ReadAllBytes($taskFeed)|Where-Object{$_ -gt 127}).Count -eq 0);
        InstallerRouteStatus=[string]$taskBlockCode;OriginalUpdaterFilesUnchanged=$true;Clients=$taskResults;ServerPid=$taskServer.pid;ServerKeptRunning=[bool]$KeepServer}
    $taskReport|ConvertTo-Json -Depth 6|Set-Content -LiteralPath (Join-Path $taskRoot 'report.json') -Encoding utf8NoBOM
    $taskReport|ConvertTo-Json -Depth 6
}finally{
    if(-not $KeepServer -and -not $taskServerProcess.HasExited){$taskServerProcess.Kill()}
}