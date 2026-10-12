<#
Invite an operator or kitchen manager to the deployed pilot workspace.

    powershell -ExecutionPolicy Bypass -File scripts\create_operator.ps1 -Email you@example.org -Site hostel-4 -Role manager

Creates the Cognito user (Cognito emails a temporary password; the first
sign-in forces a new one) and writes the site membership that the API trusts.
The password is never passed on a command line or printed by this script.
Run after scripts\deploy.ps1, with an active `aws login` session.
#>
param(
  [Parameter(Mandatory = $true)][string]$Email,
  [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_\-]{1,40}$')][string]$Site,
  [ValidateSet("manager", "operator")][string]$Role = "operator"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$aws = "aws"
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) { $aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe" }
$o = Get-Content (Join-Path $root "build\stack-outputs.json") -Raw | ConvertFrom-Json

$filter = 'email = "' + $Email + '"'
$found = & $aws cognito-idp list-users --user-pool-id $o.UserPoolId --region $o.Region --filter $filter --query "Users[0].Username" --output text
if ($LASTEXITCODE -ne 0) { throw "could not query the user pool" }
if (-not $found -or $found -eq "None") {
  & $aws cognito-idp admin-create-user --user-pool-id $o.UserPoolId --username $Email --region $o.Region `
    --user-attributes Name=email,Value=$Email Name=email_verified,Value=true --desired-delivery-mediums EMAIL --output json | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "could not create the Cognito user" }
  Write-Host "Created $Email. Cognito has emailed a temporary password."
} else {
  Write-Host "$Email already exists in the user pool."
}
$sub = & $aws cognito-idp admin-get-user --user-pool-id $o.UserPoolId --username $Email --region $o.Region --query "UserAttributes[?Name=='sub'].Value | [0]" --output text
if ($LASTEXITCODE -ne 0 -or -not $sub -or $sub -eq "None") { throw "could not read the user's id" }

# JSON goes through a file: quoting JSON on a Windows PowerShell command line is unreliable
$itemFile = Join-Path $root "build\membership-item.json"
(@{ PK = @{ S = "USER#$sub" }; SK = @{ S = "SITE#$Site" }; role = @{ S = $Role } } | ConvertTo-Json -Compress) | Out-File -Encoding ascii $itemFile
& $aws dynamodb put-item --table-name $o.TableName --region $o.Region --item "file://$itemFile"
if ($LASTEXITCODE -ne 0) { throw "could not write the site membership" }
Remove-Item $itemFile -Confirm:$false
Write-Host "$Email is now a $Role of site '$Site'. Sign in at $($o.AppUrl)/signin"
