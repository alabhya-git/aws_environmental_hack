At about half past ten on the last night of a hackathon, our first deployment to AWS failed with this:

> Your account must be verified before you can add new CloudFront resources.

By then we had also discovered that the build machine had no Docker, no AWS CLI and no AWS credentials. This post is how a full-stack app still got deployed that night, and the handful of AWS design choices we would repeat on any project.

The app is ProofPlate, our Environmental Hacks entry (Waste and Energy track). It suggests the first portion for each diner in a college mess, records what comes back on the plate, and plans how much the kitchen should cook. Under the hood there is a Bayesian model and a Monte Carlo planner, so every request does real computation. Most of the code was written with Claude Code from our specification; the constraints and decisions below are ours.

## The shape of it

- **AWS Lambda** (Python 3.13, FastAPI behind Mangum) runs the model and the planner.
- **Amazon API Gateway** (HTTP API) is the HTTPS front door, with throttling.
- **Amazon DynamoDB** holds the pilot records.
- **Amazon Cognito** signs kitchen staff in.
- **AWS SAM** describes all of it in one template.

Nothing is left running. An idle week costs nothing, and we estimate a busy demo week at under a dollar.

## No Docker, no problem

Our plan said "Lambda container image". The machine said Docker was not installed, and there was no time to install it.

A Lambda zip only needs Linux binaries, and pip will fetch Linux wheels on any operating system if you ask:

```bash
pip install --target build/lambda \
  --platform manylinux_2_28_x86_64 --implementation cp \
  --python-version 3.13 --only-binary=:all: \
  -r requirements.txt
```

That produced a working package with NumPy and pydantic-core compiled for Lambda's runtime, built on Windows. The build script fails loudly if it cannot find the Linux NumPy binary, so a wrong platform tag never reaches production.

We also left SciPy out. It would have roughly tripled the package for the sake of one function, the normal CDF. We implemented it with a Chebyshev fit of the error function, evaluated in the log domain, and wrote a test that compares it with SciPy on a developer machine. The result is 60 MB unzipped, 18 MB zipped.

SAM helped here in a way that is easy to miss: the `AWS::Serverless` transform runs inside CloudFormation. We wrote a SAM template and deployed it with `aws cloudformation package` and `deploy`, with no SAM CLI installed.

## One DynamoDB transaction that makes retries safe

A serving counter is a hostile environment for data. Phones lose signal mid-request and retry. Two staff can tap the same diner's plate at once. If a retried "plate returned" event is stored twice, the model learns from it twice.

Our rule: events are immutable, and the model's state is never stored. It is rebuilt by replaying events. So the only thing that must be right is the write, and one transaction makes it right:

```python
self.c.transact_write_items(TransactItems=[
    {"Put": {"TableName": self.table, "Item": {**idem, "ref": {"S": sk}},
             "ConditionExpression": "attribute_not_exists(PK)"}},
    {"Put": {"TableName": self.table,
             "Item": {"PK": pk, "SK": {"S": sk}, "doc": {"S": json.dumps(event)}}}},
    {"Update": {"TableName": self.table, "Key": counter,
                "UpdateExpression": "ADD n :one",
                "ConditionExpression": cond, "ExpressionAttributeValues": vals}},
])
```

Three items, all or nothing:

1. An **idempotency anchor** keyed by the client's event id, which must not exist yet. A retry fails here, and we return the event that was already stored.
2. The **event** itself, under a sort key of diner, food and time, so a diner's history is one strongly consistent query.
3. A **per-plate counter** that must equal what the caller validated against. If another station wrote to the same plate in between, this fails, and the service re-reads and re-validates instead of overwriting.

It all lives in one table: `SITE#…` partitions for records and events, `USER#…` for staff membership. Documents are stored as JSON strings, which keeps gram counts as exact integers and avoids float-to-Decimal conversion entirely.

We run one storage contract test against three adapters: in-memory, SQLite for local development, and DynamoDB through moto. After deployment, browser tests exercised the real table end to end.

## A public demo that stores nothing

Judges need to try the app without an account, and a public write endpoint next to real pilot data is an obvious risk.

So the demo has no storage at all. The browser keeps a small journal of what the visitor did. Every demo request sends that journal; the Lambda rebuilds an in-memory workspace from a fixed seed plus the journal, computes the answer, and forgets everything. "Reset demo" just clears the browser.

The demo and the real workspace run the same model code. The demo simply has no path to the DynamoDB table.

## Trust the token, never the request

Staff sign in through a Cognito user pool with admin-created accounts only. API Gateway's JWT authorizer guards the operator routes, so unauthenticated requests never reach our code.

Which kitchen someone belongs to is looked up from a membership item keyed by the token's subject. The API never reads a site id from the request body. Our request models reject unknown fields, so a forged `site_id` returns a 422 instead of being silently ignored. There is a development sign-in for local work, and the app refuses to start with it enabled anywhere but a local environment.

## The CloudFront wall

Back to half past ten. New AWS accounts cannot create CloudFront distributions until AWS Support verifies them, and that does not happen at night before a deadline.

The stack rolled back cleanly, with one surprise worth knowing: our DynamoDB table had `DeletionPolicy: Retain`, so the rollback left an empty orphan table behind. That is the policy doing its job, but you have to delete it yourself.

We had planned for this failure, which is the real lesson. The template takes a parameter:

```yaml
Conditions:
  UseCloudFront: !Equals [!Ref WithCloudFront, "true"]
```

With it set to false, the CloudFront resources are skipped and the Lambda serves the built web app itself through the API Gateway URL. One redeploy later the app was live over HTTPS. A request for a path without a file extension gets the app shell; a missing asset gets a real 404, not HTML pretending to be JavaScript.

There is a cost to this fallback. Lambda responses are capped at 6 MB, so anything large has to come from somewhere else. For us that was a 14 MB in-browser model, which the browser fetches from a public CDN.

## Two smaller things that saved time

**`aws login`.** Recent versions of AWS CLI v2 can sign in through the browser with your console credentials and cache a temporary session. Nobody created or pasted an access key that night.

**JSON through files on Windows.** Quoting on a PowerShell command line bit us more than once. Every JSON argument in our scripts now goes through `file://`.

## Proving it works, on the real thing

After each deploy we ran the same scripted browser tests against the live URL:

- The public demo at desktop and phone widths.
- A full operator flow with a throwaway Cognito user: sign in, calibrate, serve, record a return, see the suggestion change, reload, correct a record, sign out.
- A second site's operator, who must see nothing from the first.

Then the script deleted the test user and its records and checked the table was empty. Warm requests took about 0.25 s; a kitchen plan with a Monte Carlo simulation took about 1.1 s.

What we have not verified is listed in the repository too: CloudFront itself, cold-start time, and data surviving a redeploy.

## What we would repeat

- **Design the fallback before you need it.** One template parameter turned a blocked deployment into a five-minute detour.
- **Make the write correct and derive everything else.** Immutable events plus one conditional transaction removed a whole class of bugs.
- **Give the public demo no storage.** Isolation by construction beats isolation by permissions.
- **Question every heavy dependency.** Docker and SciPy both turned out to be optional.
- **Test the deployed system the way a user would, then clean up after yourself.**

Live: https://imh92ymqaa.execute-api.ap-south-1.amazonaws.com
Code, template and deployment notes: https://github.com/alabhya-git/aws_environmental_hack
