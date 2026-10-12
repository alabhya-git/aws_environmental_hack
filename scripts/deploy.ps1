<#
Deploy ProofPlate to AWS. Requires AWS CLI v2 and an active session (`aws login`).

    powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
    powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -NoCloudFront   # accounts that cannot create distributions yet
    powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -SkipWeb        # backend only

Idempotent: re-running updates the same stack. The DynamoDB table is retained
across updates and teardown, so pilot records are never dropped by a redeploy.
#>
param(
  [string]$Stage = "prod",
  [string]$Region = "ap-south-1",
  [switch]$NoCloudFront,
  [switch]$SkipWeb
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$aws = "aws"
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) { $aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe" }
$node = Join-Path (Split-Path -Parent $root) ".tools\node-v24.21.0-win-x64"
if (Test-Path $node) { $env:Path = "$node;$env:Path" }
$stack = "proofplate-$Stage"
$env:AWS_DEFAULT_REGION = $Region   # every aws call below, including the identity check, uses this region

function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

Write-Host "== identity"
$id = & $aws sts get-caller-identity --output json | ConvertFrom-Json; Check "aws sts get-caller-identity"
$account = $id.Account
Write-Host "account $account as $($id.Arn) in $Region"

$rev = "uncommitted"
if (Test-Path .git) { $rev = (git rev-parse --short HEAD); if (git status --porcelain) { $rev = "$rev-dirty" } }

if (-not $SkipWeb) {
  Write-Host "== web build"
  Push-Location apps\web
  if (-not (Test-Path node_modules)) { npm ci --no-audit --no-fund; Check "npm ci" }
  npm run build; Check "web build"
  Pop-Location
}

Write-Host "== lambda package"
python scripts\build_lambda.py; Check "lambda build"

Write-Host "== artifact bucket"
New-Item -ItemType Directory -Force build | Out-Null
$artifacts = "proofplate-artifacts-$account-$Region"
$have = & $aws s3api list-buckets --query "Buckets[?Name=='$artifacts'].Name" --output text; Check "list buckets"
if (-not $have) {
  & $aws s3api create-bucket --bucket $artifacts --region $Region --create-bucket-configuration LocationConstraint=$Region | Out-Null; Check "create artifact bucket"
  & $aws s3api put-public-access-block --bucket $artifacts --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true; Check "block public access"
  # JSON goes through a file: quoting JSON on a Windows PowerShell command line is unreliable
  $lifecycle = Join-Path $root "build\artifact-lifecycle.json"
  '{"Rules":[{"ID":"expire-old-packages","Status":"Enabled","Filter":{},"Expiration":{"Days":14}}]}' | Out-File -Encoding ascii $lifecycle
  & $aws s3api put-bucket-lifecycle-configuration --bucket $artifacts --lifecycle-configuration "file://$lifecycle"; Check "artifact lifecycle"
}

Write-Host "== package + deploy stack $stack"
& $aws cloudformation package --template-file infra\template.yaml --s3-bucket $artifacts --s3-prefix $stack --output-template-file build\packaged.yaml --region $Region | Out-Null; Check "cloudformation package"
$cf = if ($NoCloudFront) { "false" } else { "true" }
& $aws cloudformation deploy --template-file build\packaged.yaml --stack-name $stack --region $Region `
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --no-fail-on-empty-changeset `
  --parameter-overrides Stage=$Stage Revision=$rev WithCloudFront=$cf --tags Project=proofplate Stage=$Stage
Check "cloudformation deploy"

$raw = & $aws cloudformation describe-stacks --stack-name $stack --region $Region --query "Stacks[0].Outputs" --output json | ConvertFrom-Json
$out = @{}; foreach ($o in $raw) { $out[$o.OutputKey] = $o.OutputValue }
$out["Region"] = $Region; $out["Stack"] = $stack; $out["Revision"] = "$rev"; $out["DeployedAt"] = (Get-Date).ToUniversalTime().ToString("s") + "Z"
$out | ConvertTo-Json | Out-File -Encoding utf8 build\stack-outputs.json

if (-not $SkipWeb -and -not $NoCloudFront) {
  Write-Host "== publish web app to private S3"
  & $aws s3 sync apps\web\dist "s3://$($out.WebBucketName)" --delete --region $Region --exclude index.html --cache-control "public,max-age=31536000,immutable" | Out-Null; Check "s3 sync"
  & $aws s3 cp apps\web\dist\index.html "s3://$($out.WebBucketName)/index.html" --region $Region --cache-control "no-cache" --content-type "text/html; charset=utf-8" | Out-Null; Check "s3 cp index"
  & $aws cloudfront create-invalidation --distribution-id $out.DistributionId --paths "/index.html" "/" | Out-Null
}

Write-Host "== smoke test"
$health = Invoke-RestMethod -Uri "$($out.ApiUrl)/api/health" -TimeoutSec 60
Write-Host ("api: ok={0} store={1} region={2} revision={3}" -f $health.ok, $health.store, $health.region, $health.revision)
Write-Host ""
Write-Host "App URL : $($out.AppUrl)"
Write-Host "API URL : $($out.ApiUrl)"
Write-Host "Outputs : build\stack-outputs.json"
