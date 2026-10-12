# Deployment

## Status

**Deployed and verified on AWS, without CloudFront.**

| | |
|---|---|
| App and API | https://imh92ymqaa.execute-api.ap-south-1.amazonaws.com |
| Region | ap-south-1 (Mumbai) |
| Stack | `proofplate-prod` (CloudFormation, SAM) |
| Deployed revision | code at `10092ce` (before/after photo estimate); later commits are documentation only |
| Deployed | 12 October 2026, about 02:40 IST. Earlier deploys: 11 October, 22:28 and 22:57 |

**Why no CloudFront.** The first deploy failed at the CloudFront distribution: *"Your account must be verified before you can add new CloudFront resources"* (AWS Support verification is needed on a new account). The stack was rolled back and redeployed with `-NoCloudFront`. The web app is therefore served by the Lambda through the API Gateway HTTPS URL, which is a supported fallback in the template. The S3 bucket and Origin Access Control from the full design are not used in this deployment, and the web bucket created by the stack is empty. Once the account is verified, `scripts\deploy.ps1` without `-NoCloudFront` adds CloudFront and publishes the app to S3.

### What was verified on the live deployment

| Check | Result |
|---|---|
| `GET /api/health` | `ok`, store `dynamo`, region `ap-south-1`, Lambda function name returned |
| Deep link `/demo/kitchen` | 200, HTML app shell |
| Missing asset `/assets/nope.js` | 404, JSON (not the app shell) |
| Pilot API without a token | 401 |
| Pilot API with the local-dev header `x-dev-user` | 401 (development sign-in is not available on AWS) |
| Public demo browser walkthrough (desktop 1366 px and phone 390 px) | passed against the live URL |
| Pilot browser flow with real Cognito sign-in: calibrate, serve, seconds, return, changed suggestion, reload, correction, evidence, sign out | passed; records written to and read from DynamoDB |
| Tenant isolation: operator of a second site | sees 0 foods, services and diners; a forged `site_id` in the body is rejected with 422 |
| Warm latency from the build machine, Mumbai region, including network | demo bootstrap about 0.25 s; kitchen plan about 1.1 s |
| Redesigned interface (second deploy) | public walkthrough passed at desktop and phone widths; Cognito pilot flow passed again |
| Before/after photo estimate (third deploy) | photo flow passed against the live URL with the segmentation model loaded from its public CDNs (automatic outlines, every correction tool, 36 g +/- 14 against a generated truth of 27 g) and with those CDNs blocked (by hand) |
| One real photograph on the live site (white rice in a steel bowl) | bowl and rice outlined automatically; 34.6% of the plate in the before slot and 34.2% in the after slot |
| A photo-estimate return through the signed-in API | stored in DynamoDB with its `photo_detail`; counted under return methods in Evidence; used for learning with a wider tolerance |
| Service creation with a chosen menu | passed in the live pilot flow |
| Self-hosted fonts, the worker script and sample photos | served from the same origin (200) |
| Test data | a throwaway operator and records were used each time, then deleted; the table and user pool are empty |

### Not verified

- CloudFront, S3 publishing and the deep-link rewrite function (not created).
- The photo estimate's grams against a scale. No real before and after pair with weighed leftovers has been tested, and no phone camera has been used on the deployed site.
- The photo estimate when jsDelivr or Hugging Face are slow or unreachable from the user's network, beyond the scripted by-hand fallback.
- Cold-start time (not measured separately).
- Records surviving a redeploy: the table has a Retain policy and was not replaced by the second deploy, but a redeploy with data in it was not tested.
- Deployment from a second AWS account.
- The deployment ran as the account's root user; use a least-privilege IAM user for routine redeploys.

## What gets created

One CloudFormation stack, `proofplate-prod`, in `ap-south-1`, from `infra/template.yaml` (AWS SAM).

| Resource | Purpose | Notes |
|---|---|---|
| CloudFront distribution | Public HTTPS entry point | Default CloudFront hostname; no custom domain |
| S3 bucket (web) | Built web app | Private; read only by CloudFront through Origin Access Control |
| CloudFront function | Sends extension-less paths to the app shell | A missing asset stays an error, not HTML |
| API Gateway HTTP API | `/api/*` | Throttled at 20 requests/s, burst 40 |
| Lambda function | FastAPI app: model, policy, planner | python3.13, 1536 MB, 25 s timeout |
| DynamoDB table | Pilot records | On-demand, encrypted, point-in-time recovery, **retained on teardown** |
| Cognito user pool + client | Operator sign-in | Admin-created users only; JWT authorizer on `/api/pilot/*` |
| CloudWatch log group | Request logs | 14-day retention; no diner payloads |
| S3 bucket (artifacts) | Deployment packages | Created by the deploy script; objects expire after 14 days |

IAM: the function's role may read and write only its own table. No credentials are bundled in the web app; its runtime settings come from `/api/config`.

Not used: NAT gateways, VPCs, containers or Kubernetes, GPUs, provisioned capacity, foundation models.

## Cost estimate

For one week at about 1,000 visits and 10,000 API calls, using public on-demand list prices for `ap-south-1` as understood at build time. **Check current pricing before relying on this.** Promotional credits and free-tier allowances are not counted.

| Service | Assumed usage | Rough cost |
|---|---|---|
| Lambda | 10,000 requests × ~0.5 s × 1.5 GB ≈ 7,500 GB-s | about $0.13 |
| API Gateway HTTP API | 10,000 requests | about $0.01 |
| DynamoDB on-demand | a few thousand reads and writes, < 10 MB stored | under $0.01 |
| CloudFront | ~1,000 visits × ~0.35 MB ≈ 0.35 GB out | about $0.04 |
| S3 | < 100 MB stored | under $0.01 |
| Cognito | a handful of operators | $0 at this scale |
| CloudWatch Logs | < 50 MB | about $0.03 |
| **Total** | | **well under US$1 for the week** |

This is an estimate, not a bill and not a spending cap. A budget alert in AWS Budgets is recommended; it notifies, it does not stop spending.

## Deploy

Requires AWS CLI v2.32+ with an active session, Python 3.13 and Node 20+. No Docker.

```powershell
aws login                                                       # browser sign-in; region ap-south-1
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
```

The script: checks identity → builds the web app → assembles the Lambda package → creates the artifact bucket if needed → packages and deploys the stack → uploads the web app to the private bucket → invalidates `index.html` → calls `/api/health` → writes `build\stack-outputs.json`.

If the account cannot create CloudFront distributions yet (new accounts sometimes need verification), deploy with `-NoCloudFront`. The Lambda then serves the web app through the API Gateway URL.

## Invite an operator

```powershell
powershell -ExecutionPolicy Bypass -File scripts\create_operator.ps1 -Email you@example.org -Site hostel-4 -Role manager
```

Cognito emails a temporary password; the first sign-in sets a new one. The script writes the site membership the API trusts. No password is passed on a command line or stored in the repository.

## Verify a deployment

```powershell
$o = Get-Content build\stack-outputs.json | ConvertFrom-Json
python tests\e2e\walkthrough.py $o.AppUrl                           # public demo, desktop and phone
$env:PP_PASSWORD = Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText   # PowerShell 7; or set it in your shell
python tests\e2e\pilot_flow.py $o.AppUrl --user you@example.org     # pilot workspace
```

## Rollback

Packages are stored under a content hash in the artifact bucket for 14 days, and the function's `PROOFPLATE_REVISION` records the git revision.

```powershell
git checkout <previous-revision>
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
```

A failed stack update rolls back by itself. Pilot records are untouched by any redeploy.

## Teardown

```powershell
$o = Get-Content build\stack-outputs.json | ConvertFrom-Json
aws s3 rm "s3://$($o.WebBucketName)" --recursive --region $o.Region
aws cloudformation delete-stack --stack-name $o.Stack --region $o.Region
aws cloudformation wait stack-delete-complete --stack-name $o.Stack --region $o.Region
```

The DynamoDB table is **kept** on purpose. To delete pilot data as well:

```powershell
aws dynamodb delete-table --table-name $o.TableName --region $o.Region
```

Also remove the artifact bucket `proofplate-artifacts-<account>-<region>` if it is no longer needed.
