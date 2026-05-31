# Blog walk: `ai-assisted-aws-intrusion`

A manual ground-truth reading of Sysdig's "AI-assisted cloud intrusion"
post, mapping the blog's narrative onto the AttackSpec structure
(`docs/schema.md §4.8`).

---

## 1. Header

- **id:** `ai-assisted-aws-intrusion` (matches `eval/blog-sets/manifest.yaml`)
- **shape:** `incident_analysis`
- **URL:** https://www.sysdig.com/blog/ai-assisted-cloud-intrusion-achieves-admin-access-in-8-minutes
- **Canonical URL:** same as above (no observed redirect)
- **Title:** "AI-assisted cloud intrusion achieves admin access in 8 minutes"
- **Publisher:** Sysdig (Sysdig Threat Research Team blog)
- **Publisher kind:** `vendor_lab`
- **Authors:** Alessandro Brucato, Michael Clark
- **Publication date:** 2026-02-03
- **Accessed date:** 2026-05-20
- **Content hash:** TBD (Extractor produces mechanically; not computed for this walk)

## 2. One-paragraph summary

Sysdig researchers reconstructed an offensive campaign in which the
threat actor obtained valid AWS test credentials stolen from publicly
accessible S3 buckets holding retrieval-augmented generation (RAG) data
for AI models, then escalated to administrator access in approximately
eight minutes by injecting code into an existing Lambda function
(`EC2-init`) — exercising both `UpdateFunctionCode` and
`UpdateFunctionConfiguration` permissions — to create access keys for
the admin user `frick` (after a failed first attempt against `adminGH`,
which despite its name lacked admin privileges). From there they
assumed six IAM roles across 14 sessions, gained access to five IAM
users total (four pre-existing accounts taken over by issuing new
access keys — including one with `BedrockFullAccess` and one named
`AzureADRoleManager` — plus a newly created `backdoor-admin` with
`AdministratorAccess`), and exfiltrated secrets, SSM parameters,
CloudWatch logs, Lambda source code, internal S3 data, CloudTrail
events, and IAM Access Analyzer findings. The attackers also performed
LLMjacking against AWS Bedrock (invoking models 13 times across nine
distinct models, with some invocations leveraging Bedrock's
cross-Region inference feature, after verifying that Bedrock
model-invocation logging was already disabled), attempted five times to
launch a `p5.48xlarge` GPU instance (which failed with "Insufficient
capacity") before successfully launching a `p4d.24xlarge`
(~$32.77/hour, ~$23,600/month) with a shared 2 TB EBS volume, key pair
`stevan-gpu-key`, and a security group permitting any-IP all-TCP
ingress — terminated after 5 minutes — and staged a Terraform module
named `terraform-bedrock-deploy.tf` configured to deploy a backdoor
Lambda for generating Bedrock credentials. Several technical tells —
non-English source comments suggesting Serbian-speaking origin,
attempted role assumptions in AWS account IDs that did not belong to
the organization (two pattern-based hallucinations plus one that may
correspond to a real external account), and a hallucinated GitHub
repository URL — collectively suggest LLM-assisted operation, with
Sysdig attributing the Serbian comments specifically to attacker origin
and the GitHub-URL hallucination specifically to LLM generation.

## 3. Thesis

Per `schema.md §4.8` lines 316–333.

- **Types:** `ttp_chain`, `incident_analysis`, `privilege_escalation`.
- **Summary:** An attacker with limited AWS credentials sourced from a
  publicly readable S3 bucket escalated to administrator access in
  approximately eight minutes by abusing a Lambda function's
  execution-role permissions (via `UpdateFunctionCode` and
  `UpdateFunctionConfiguration`), then expanded into a multi-objective
  campaign — identity-control persistence, mass data collection,
  Bedrock LLMjacking, GPU resource abuse, and staging of a Terraform
  backdoor — over a roughly two-hour window, with several tells
  consistent with LLM assistance.
- **Attacker objective:** Maximize control of and value extracted from
  the compromised AWS account: establish persistent administrator
  identities, exfiltrate secrets and operational telemetry, and operate
  Bedrock and GPU resources at the victim's expense.
- **Vulnerability story:** Not applicable in the vulnerability-disclosure
  sense — no CVE is involved. The root cause is misconfiguration
  (credentials in a public S3 bucket containing RAG data) compounded by
  an over-privileged Lambda execution role that permitted IAM
  `CreateAccessKey` against an unrelated high-privilege user. Recorded
  explicitly as empty per the template's instruction.

## 4. Chain steps

The ordered list of attacker actions Sysdig describes. Eight discrete
steps; step 8 (Terraform backdoor) is included because the blog
narrates it as part of the canonical campaign even though the module
was staged but never fully deployed.

### Chain step 1: Initial access via credentials in a public S3 bucket

- **Blog excerpt:**
  > "The threat actor infiltrated the victim's environments using valid
  > test credentials stolen from public S3 buckets. These buckets
  > contained Retrieval-Augmented Generation (RAG) data for AI models,
  > and the compromised credentials belonged to an Identity and Access
  > Management (IAM) user that had multiple read and write permissions
  > on AWS Lambda and restricted permissions on AWS Bedrock."
- **Additional verbatim** (bolded in source — Sysdig's framing of
  intent; the timeline section identifies this IAM user literally as
  `compromised_user`):
  > "This user was likely intentionally created by the victim
  > organization to automate Bedrock tasks with Lambda functions
  > across the environment."
- **Additional verbatim** (S3 bucket naming-convention reconnaissance):
  > "It is also important to note that the affected S3 buckets were
  > named using common AI tool naming conventions, which the attackers
  > actively searched for during reconnaissance."
- **Description:** Attacker reads long-lived IAM user credentials
  (literal user identifier in the blog's timeline: `compromised_user`)
  from publicly accessible S3 buckets that held RAG data for AI
  models. The compromised user has read/write Lambda permissions and
  restricted Bedrock permissions — likely intentionally provisioned by
  the victim organization to automate Bedrock-via-Lambda tasks. The
  bucket discovery itself was reconnaissance-driven: the attacker
  searched for S3 buckets whose names matched common AI-tool naming
  conventions.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Public S3 bucket containing a file with valid IAM
  user credentials; the IAM user has Lambda permissions (including
  `lambda:UpdateFunctionCode` and `lambda:UpdateFunctionConfiguration`)
  and restricted Bedrock permissions.
- **Postconditions:** Attacker holds valid `aws_credentials` for an IAM
  user; can call AWS APIs as that principal.
- **Outputs (value-type names from `schema.md §4.12`):**
  - `aws_credentials` (exists in registry)
  - `[PROPOSED: aws_iam_user_arn]` — the principal ARN those credentials
    resolve to is needed by later steps; not in the v1 registry seed.
- **Reproducibility:** `partial_simulation`
- **Why this tier (not a higher one):** A lab can seed an S3 bucket with
  mock credentials so the "find creds in public RAG bucket" beat is
  real, but the original organizational misconfiguration that exposed
  real RAG data cannot be reproduced and the test-IAM-user setup must
  be lab-fabricated rather than discovered.

### Chain step 2: Service enumeration across ten AWS services

- **Blog excerpt:**
  > "They enumerated resources across multiple AWS services, including:
  > Secrets Manager, Systems Manager (SSM), S3, Lambda, Elastic Compute
  > Cloud (EC2), Elastic Container Service (ECS), Organizations,
  > Relational Database Service (RDS), CloudWatch, Key Management
  > Service (KMS)."
- **Description:** Attacker fans out Describe/List/Get calls across IAM,
  compute, data, and organization services to map the environment. The
  blog enumerates ten services explicitly, including Organizations.
  Separately, the blog also notes the attacker investigated AI
  services: a Bedrock enumeration block (foundation models, custom
  models, knowledge bases), plus OpenSearch Serverless
  (`ListCollections`, `ListAccessPolicies`) and SageMaker
  (`ListModels`, `ListEndpoints`, `ListTrainingJobs`).
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Valid credentials from step 1; the credentials'
  attached policies permit `Describe*`, `List*`, and `Get*` against the
  enumerated services.
- **Postconditions:** Attacker has an inventory of Lambda functions,
  secrets identifiers, SSM parameters, organizational structure, and
  related compute/data resources.
- **Outputs:** `[PROPOSED: aws_lambda_reference]`,
  `[PROPOSED: aws_secrets_manager_inventory]`,
  `[PROPOSED: aws_ssm_inventory]`,
  `[PROPOSED: aws_organizations_inventory]`. None of these are in the
  v1 registry seed; each is a list-shaped inventory type the blog
  clearly surfaces.
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Direct AWS API enumeration with
  restricted credentials is fully reproducible against real AWS; the
  lab only needs to provision the target services for there to be
  something to enumerate.

### Chain step 3: Privilege escalation via Lambda code injection

- **Blog excerpt:**
  > "Since the compromised user had `UpdateFunctionCode` and
  > `UpdateFunctionConfiguration` permissions on Lambda, the threat
  > actor pivoted to privilege escalation through Lambda function code
  > injection. They replaced the code of an existing Lambda function
  > named `EC2-init` three times, iterating on their target user."
- **Additional verbatim attribution** (Sysdig describes the Serbian
  comments separately from any LLM-generation inference, attributing
  them only to attacker origin):
  > "The comments in the code are written in Serbian, likely suggesting
  > the threat actor's origin."
- **Description:** Attacker overwrites the source of an existing
  Lambda function so that its execution role's IAM permissions are
  repurposed to call `iam:CreateAccessKey` against a separate,
  higher-privilege user. Per the blog's timeline, the attacker also
  increased the function's timeout from 3 to 30 seconds to give the
  injected code room to run. Takes three iterations to converge on the
  correct payload: the first iteration targeted `adminGH` (whose name
  implied admin but whose privileges did not match); subsequent
  attempts succeeded in compromising the admin user `frick`.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Valid credentials with `lambda:UpdateFunctionCode`
  and `lambda:UpdateFunctionConfiguration` against a target function
  whose execution role has `iam:CreateAccessKey` on a higher-privilege
  user.
- **Postconditions:** Attacker holds a fresh `aws_credentials` pair for
  the admin IAM user `frick` (administrator-equivalent privileges).
- **Outputs:** `aws_credentials` (a second, higher-privilege pair),
  `[PROPOSED: aws_iam_user_arn]` (`frick` — the escalation-target
  principal).
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** `UpdateFunctionCode`,
  `UpdateFunctionConfiguration`, `lambda:Invoke`, and
  `iam:CreateAccessKey` are real, lab-runnable API calls; the lab
  provisions a victim Lambda function with the right execution role and
  the seeded high-privilege user. The "three iterations before it
  works" detail is illustrative but not required for reproducibility.

### Chain step 4: Lateral movement via role assumption and user creation

- **Blog excerpt:**
  > "The threat actor assumed six different IAM roles across 14
  > different sessions. Additionally, they gained access to five IAM
  > users, resulting in a total of 19 unique AWS principals involved in
  > the attack."
- **Additional verbatim** (decomposes how the five IAM users break
  down — four pre-existing accounts taken over plus one newly created;
  reproduced as a single contiguous blog passage to preserve the
  intermediate sentence about `BedrockFullAccess` / `AzureADRoleManager`):
  > "Four of the five compromised IAM users already existed in the
  > victim's account; the threat actor took over them by creating new
  > access keys. Among those users, one had the `BedrockFullAccess`
  > policy attached, while another had a name (`AzureADRoleManager`)
  > suggesting it was used for integration with an Azure account. The
  > threat actor then created a new user, `backdoor-admin`, and
  > attached the `AdministratorAccess` policy to it."
- **Additional verbatim** (the hallucinated cross-account `AssumeRole`
  attempts):
  > "they included account IDs that did not belong to the organization:
  > two IDs with ascending and descending digits (`123456789012` and
  > `210987654321`), and one ID that may belong to a real external
  > account (`653711XXXXXX`)."
- **Description:** Holding `frick`'s admin credentials, the attacker
  takes over four existing IAM users by issuing new access keys —
  including `rocker` (which had the `BedrockFullAccess` policy
  attached) and `AzureADRoleManager` (whose name suggests it was used
  for Azure integration) — creates a fifth IAM user `backdoor-admin`
  and attaches `AdministratorAccess`, and chains `AssumeRole` calls
  across six roles and 14 sessions. The timeline section names
  specific role-assumption attempts: at 0:06:00 the attacker failed to
  assume `admin` and `Administrator` but successfully assumed
  `sysadmin`, `netadmin`, and `account`; at 1:21:00 the attacker
  successfully assumed `sysadmin`, `developer`, and `external` while
  failing on `EKS-access`. Separately, several `AssumeRole` calls
  target account IDs that do not belong to the organization, including
  two pattern-based IDs (`123456789012`, `210987654321`) and one that
  may correspond to a real external account (`653711XXXXXX`).
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Admin-equivalent credentials from step 3; trust
  policies on in-account roles permitting the escalated user to assume
  them.
- **Postconditions:** Persistent `backdoor-admin` identity with
  `AdministratorAccess`; new long-lived access keys on four existing
  IAM users; multiple active session credentials across six roles.
- **Outputs:** `[PROPOSED: aws_iam_user_arn]` (`backdoor-admin` plus
  the four taken-over users), `[PROPOSED: aws_iam_role_arn]` (each
  assumed role), `[PROPOSED: aws_temporary_credentials]` (session
  credentials returned by `AssumeRole`; distinct from `aws_credentials`
  which is long-lived).
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** `iam:CreateUser`,
  `iam:CreateAccessKey`, `iam:AttachUserPolicy`, and `sts:AssumeRole`
  are core, lab-runnable APIs; the lab can seed multiple roles with
  appropriate trust policies and multiple existing IAM users for the
  takeover beat. Failed `AssumeRole` calls against non-existent
  accounts produce real API error responses against real AWS.

### Chain step 5: Data exfiltration across secrets, parameters, logs, and findings

- **Blog excerpt:**
  > "Using their newly created admin user, the threat actor collected
  > data across multiple services: Secrets from Secrets Manager, SSM
  > parameters from EC2 Systems Manager, CloudWatch logs, Lambda
  > function source code, Internal data from S3 buckets, CloudTrail
  > events"
- **Additional verbatim** (IAM Access Analyzer enumeration, including
  the three finding categories Sysdig names):
  > "Beyond stealing resource data, they enumerated IAM Access Analyzer
  > findings. Access Analyzer provides three types of findings:
  > external access findings show resources accessible outside the zone
  > of trust; internal access findings reveal possible access paths
  > between IAM users/roles and specified resources; and unused access
  > findings return information about unused roles, permissions, and
  > credentials."
- **Description:** Operating as `backdoor-admin`, attacker reads
  sensitive material from Secrets Manager, SSM Parameter Store,
  CloudWatch Logs, Lambda function source, S3, and CloudTrail, then
  enumerates IAM Access Analyzer findings (external-access,
  internal-access, and unused-access categories) to map the
  environment further.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Admin credentials from step 4 with `Get*`
  permissions across the listed services and `access-analyzer:List*`
  for findings.
- **Postconditions:** Attacker possesses a dump of the account's secret
  material, operational telemetry, and Access Analyzer findings.
- **Outputs:** `[PROPOSED: aws_secret_value]`,
  `[PROPOSED: aws_ssm_parameter]`,
  `[PROPOSED: cloudwatch_log_entries]`,
  `[PROPOSED: lambda_source_code]`,
  `[PROPOSED: s3_object_contents]`,
  `[PROPOSED: cloudtrail_event_dump]`,
  `[PROPOSED: iam_access_analyzer_findings]` (further decomposable into
  external-access, internal-access, unused-access subtypes per the
  blog's framing).
- **Reproducibility:** `partial_simulation`
- **Why this tier (not a higher one):** `GetSecretValue`,
  `GetParameter`, `GetObject`, `GetTrailStatus`, and Access Analyzer
  reads are real APIs the lab can exercise, but the lab must seed mock
  data rather than exposing real victim data; "exfil" is structurally
  faithful but the content is fabricated.

### Chain step 6: LLMjacking — Bedrock model invocations 13 times across 9 models

- **Blog excerpt:**
  > "They invoked Bedrock models 13 times, including Claude Sonnet 4,
  > Claude Opus 4, Claude 3.5 Sonnet, Claude 3 Haiku, DeepSeek R1, Llama
  > 4 Scout, Amazon Nova Premier, Amazon Titan Image Generator, and
  > Cohere Embed v3."
- **Additional verbatim** (Bedrock logging precondition — already
  disabled before the campaign; attacker verified rather than disabling):
  > "they checked whether the account had model invocation logging
  > enabled by calling `GetModelInvocationLoggingConfiguration`. This
  > check has been observed in previous LLMjacking attacks."
  > "After verifying that logging was disabled, the threat actor
  > invoked multiple AI models"
- **Additional verbatim** (cross-Region inference, main narrative —
  the literal "In some cases" qualifier scopes the feature to a subset
  of invocations):
  > "In some cases, they leveraged cross-Region inference, which
  > distributes model invocation requests across different Regions to
  > increase throughput and performance."
- **Additional verbatim** (cross-Region inference, timeline section —
  contradicts the main-narrative qualifier; appears in the 0:58:00 –
  1:51:00 timeline entry that also lists the 9 models):
  > "They leveraged cross-Region inference throughout."
- **Additional verbatim** (AWS Marketplace API call required for
  certain Claude models — from the same timeline entry):
  > "Invoking certain Claude models required calling
  > `AcceptAgreementRequest`."
- **Description:** Attacker first calls
  `GetModelInvocationLoggingConfiguration` to verify Bedrock
  model-invocation logging is already disabled (a recognized
  LLMjacking reconnaissance pattern), then performs 13
  `bedrock:InvokeModel` calls against 9 distinct foundation models.
  Invoking certain Claude models required additionally calling
  `AcceptAgreementRequest` (an AWS Marketplace API), which the blog
  notes as part of the attacker's flow. The blog gives two slightly
  contradictory characterizations of cross-Region inference use: the
  main-narrative sentence opens "In some cases, they leveraged
  cross-Region inference…" while the timeline-section sentence states
  "They leveraged cross-Region inference throughout." Walker preserved
  both verbatim quotes (see additional excerpts below) and flags this
  as a within-source inconsistency rather than picking one.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Bedrock service-access enabled on the account;
  Bedrock model-invocation logging in the disabled state; attacker
  holds credentials with `bedrock:InvokeModel` and
  `bedrock:GetModelInvocationLoggingConfiguration`.
- **Postconditions:** Attacker has consumed 13 Bedrock inference units
  across nine models (cost shifted to the victim); logging configuration
  remains in its prior (disabled) state.
- **Outputs:** `[PROPOSED: bedrock_inference_invocation]`,
  `[PROPOSED: bedrock_logging_configuration]`.
- **Reproducibility:** `partial_simulation`
- **Why this tier (not a higher one):** `InvokeModel` and
  `GetModelInvocationLoggingConfiguration` are real APIs the lab can
  call, but Bedrock per-model access enablement is account-and-region
  scoped, and the specific nine models the attacker invoked (Claude
  Sonnet 4, Claude Opus 4, Claude 3.5 Sonnet, Claude 3 Haiku, DeepSeek
  R1, Llama 4 Scout, Amazon Nova Premier, Amazon Titan Image Generator,
  Cohere Embed v3) may not all be available in any given lab region or
  account. Cross-Region inference is provisionable. Structurally
  faithful, but model coverage degrades.

### Chain step 7: Resource abuse — GPU launch attempts and successful `p4d.24xlarge`

- **Blog excerpt:**
  > "the threat actor attempted to launch a `p5.48xlarge` instance
  > named `stevan-gpu-monster` five times. P5 instances are
  > high-performance GPU-based instances designed for deep learning and
  > high-performance computing (HPC) applications. Those attempts
  > failed with an 'Insufficient capacity' error, prompting them to try
  > a lighter instance type. They successfully launched a
  > `p4d.24xlarge` instance ($32.77/hour, or roughly $23,600/month if
  > left running) with a shared 2 TB Elastic Block Store (EBS) volume.
  > The `SharedSnapshotVolumeCreated` log was present in CloudTrail."
- **Additional verbatim** (key pair + security group — single blog
  sentence):
  > "Then, they created a key pair named `stevan-gpu-key` and a
  > security group with an inbound rule allowing traffic from any IP
  > address to all TCP ports."
- **Additional verbatim** (instance termination, including the
  trailing "but it is unclear why" qualifier):
  > "The threat actor terminated the instance after 5 minutes, but it
  > is unclear why."
- **Description:** Attacker calls `ec2:RunInstances` five times against
  `p5.48xlarge` (instance name `stevan-gpu-monster`); each call fails
  with "Insufficient capacity." Attacker then falls back to
  `p4d.24xlarge`, which launches successfully (~$32.77/hour,
  ~$23,600/month if left running). Attacker also creates a key pair
  `stevan-gpu-key` and a security group permitting any-source TCP
  ingress across all ports. Instance is terminated after 5 minutes.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Credentials with `ec2:RunInstances`,
  `ec2:CreateKeyPair`, `ec2:CreateSecurityGroup`,
  `ec2:AuthorizeSecurityGroupIngress`; account has at least
  `p4d.24xlarge` quota approved (p5.48xlarge quota apparently not
  available in the targeted region/account at attack time).
- **Postconditions:** Running GPU instance billable to the victim
  (briefly — 5 minutes), associated EBS volume, key pair, and
  permissive security group remain.
- **Outputs:** `[PROPOSED: aws_ec2_instance_reference]`,
  `[PROPOSED: aws_ebs_volume_reference]`,
  `[PROPOSED: aws_ec2_key_pair_reference]`,
  `[PROPOSED: aws_security_group_rule_reference]`.
- **Reproducibility:** `demonstration_only`
- **Why this tier (not a higher one):** `p4d.24xlarge` costs USD
  32.77/hr and typically requires explicit AWS service-quota approval;
  running it in a lab is expensive and gated. A meaningful demonstration
  can show the `RunInstances` request shape, the
  "Insufficient capacity" failure mode for `p5.48xlarge`, the
  successful `p4d.24xlarge` request, the security-group rule shape
  (any-IP all-TCP), the key-pair creation, and the would-be cost —
  preserving the technique mechanics without actually launching the GPU
  instance. Step is not dropped (`not_reproducible`) because the
  demonstration meets the "meaningful bar" of `schema.md §4.20` (the
  user learns the API shape, the failure mode, and the cost
  implications).

### Chain step 8: Staged Terraform backdoor for Bedrock-credential vending

- **Blog excerpt:**
  > "One file of interest was a Terraform module named
  > `terraform-bedrock-deploy.tf,` which is designed to deploy a
  > backdoor Lambda function for generating Bedrock credentials."
- **Additional verbatim** (the module's attached IAM policies):
  > "The module creates a Lambda execution role with
  > `AWSLambdaBasicExecutionRole`, `IAMFullAccess`, and
  > `AmazonBedrockFullAccess` policies attached."
- **Additional verbatim** (environment variables and target user):
  The module sets `GENERATE_CREDENTIALS = "true"` and
  `TARGET_USER = "claude-bedrock-access"`.
- **Additional verbatim** (the hallucinated GitHub URL — Sysdig's
  evidence specifically for LLM generation, distinct from the Serbian
  comments which are evidence for attacker origin):
  > "The script appears designed for ML training, though the
  > hallucinated GitHub repository,
  > `https://github.com/anthropic/training-scripts.git,` does not
  > exist. This suggests that the code was LLM-generated."
- **Description:** Attacker stages (uploads / leaves in the
  environment) a Terraform module named `terraform-bedrock-deploy.tf`
  that would, if applied, create a Lambda function with an
  over-privileged execution role (`AWSLambdaBasicExecutionRole` +
  `IAMFullAccess` + `AmazonBedrockFullAccess`) and a Lambda Function
  URL configured to vend credentials for a target user
  `claude-bedrock-access` when the `GENERATE_CREDENTIALS` env var is
  `"true"`. An associated ML-training-flavoured script references a
  GitHub repository (`github.com/anthropic/training-scripts.git`) that
  does not exist, which Sysdig cites as evidence the code is
  LLM-generated.
- **MITRE techniques:** Not stated in blog.
- **Preconditions:** Credentials with sufficient permissions to write
  the staged module file (and, hypothetically, to later apply it:
  `lambda:CreateFunction`, `lambda:CreateFunctionUrlConfig`,
  `iam:CreateRole`, `iam:AttachRolePolicy`).
- **Postconditions:** Staged Terraform module present in the
  environment; the corresponding AWS resources (Lambda, role, Function
  URL) were **not** created — the backdoor was not deployed.
- **Outputs:** `[PROPOSED: terraform_module_reference]`,
  `[PROPOSED: aws_lambda_url_config]` (the would-be configuration; not
  realized in the observed incident).
- **Reproducibility:** `partial_simulation`
- **Why this tier (not a higher one):** Terraform `plan` and a scoped
  `apply` are runnable against real AWS to create a Lambda with a
  public Function URL using the policies the blog names; the lab can
  faithfully reproduce the "stand up a public cred-vending endpoint"
  mechanic. Tier is `partial_simulation` rather than `full` because the
  blog itself describes the real-world deployment as never having
  occurred (only the file was staged) — the lab will go further than
  the blog narrates, which is a divergence worth marking.

## 5. Alternative paths (optional)

Not applicable for this blog — the chain is canonical and unbranched.
The blog presents the campaign as a single observed sequence with
variations of the same primitive (multiple Lambda code-injection
iterations, five failed `p5.48xlarge` launch attempts followed by a
successful `p4d.24xlarge`, multiple `AssumeRole` calls including
attempts against non-existent accounts) collapsed inside the relevant
canonical steps.

## 6. Facets

Per `schema.md §4.13`. Walker's best estimate; categories tagged with
who is *architecturally* responsible for them.

### 6.1 `target:*` (Extractor-derived)

- `target:aws` (exists in `registry/facets.yaml`)
- `[PROPOSED: target:aws_iam]` — chain steps 3 and 4 act directly on
  IAM primitives (`CreateAccessKey`, `CreateUser`, `AttachUserPolicy`,
  `AssumeRole`); a sub-target facet captures this scope.
- `[PROPOSED: target:aws_lambda]` — chain step 3 is the central pivot
  and is Lambda-specific; step 8 also targets Lambda for the
  cred-vending backdoor.
- `[PROPOSED: target:aws_bedrock]` — chain step 6 (LLMjacking) targets
  Bedrock specifically and the blog frames it as a distinct objective.
- `[PROPOSED: target:aws_ec2]` — chain step 7 targets EC2 quota and
  instance pricing.
- `[PROPOSED: target:aws_s3]` — initial access vector and exfiltration
  target.
- `[PROPOSED: target:aws_secrets_manager]` — chain step 5 reads Secrets
  Manager material.
- `[PROPOSED: target:aws_organizations]` — chain step 2 enumerates
  AWS Organizations.
- `[PROPOSED: target:aws_iam_access_analyzer]` — chain step 5
  enumerates Access Analyzer findings specifically.

### 6.2 `runtime:*` (Planner-derived; walker's guess)

- `[PROPOSED: runtime:aws]` — first-class in `schema.md §4.13` text but
  not yet in `registry/facets.yaml`; the lab provisions and runs against
  real AWS.

No best-effort runtimes apply.

### 6.3 `lab_class_signal:*` (mostly Extractor-derived; some split)

- `[PROPOSED: lab_class_signal:incident_analysis]` — blog narrates a
  reconstructed observed campaign, not a how-to.
- `[PROPOSED: lab_class_signal:requires_infra]` — every chain step
  requires provisioned AWS resources (Lambda, IAM users/roles, S3
  bucket, Bedrock model access, EC2 quota).
- `[PROPOSED: lab_class_signal:produces_world_state]` — many steps
  create persistent state in the target account (new IAM user, new
  access keys for four existing users, EC2 instance + key pair +
  security group + EBS volume, staged Terraform module file) that must
  be cleaned up.
- `[PROPOSED: lab_class_signal:expected_detections]` — the blog
  explicitly names eleven Sysdig Secure detection rules per phase; the
  detection coverage maps onto specific chain steps.
- `[PROPOSED: lab_class_signal:manual_prereq]` — Bedrock per-model
  access enablement is account-and-region scoped and typically requires
  a manual opt-in click in the AWS console before any model can be
  invoked. The lab also requires that Bedrock model-invocation logging
  be in the disabled state (which is the AWS default; flagged for
  awareness).

## 7. Value types referenced

Registry keys from `registry/value_types.yaml`. The v1 bundled registry
ships only `aws_credentials`; all other types this walk surfaces are
flagged `(proposed)` with a one-sentence justification.

- `aws_credentials` (exists) — used for the long-lived IAM user
  credentials stolen from the public S3 bucket and re-used for the
  escalated user and for the four existing IAM users taken over via
  `CreateAccessKey` in step 4.
- `[PROPOSED: aws_iam_user_arn]` (proposed) — the principal ARN that a
  credential pair resolves to; downstream steps depend on naming
  specific users (the escalated user, `backdoor-admin`, the four
  taken-over users).
- `[PROPOSED: aws_iam_role_arn]` (proposed) — each of the six roles
  assumed in step 4 needs a stable identifier.
- `[PROPOSED: aws_temporary_credentials]` (proposed) — short-lived
  credentials returned by `sts:AssumeRole`; structurally different from
  `aws_credentials` (includes session token, has expiry).
- `[PROPOSED: aws_lambda_reference]` (proposed) — identifies a specific
  Lambda function (`EC2-init`) by ARN/name.
- `[PROPOSED: aws_secret_value]` (proposed) — opaque secret material
  retrieved via `GetSecretValue` in step 5.
- `[PROPOSED: aws_ssm_parameter]` (proposed) — SSM parameter material
  retrieved in step 5.
- `[PROPOSED: bedrock_inference_invocation]` (proposed) — the act of
  calling `bedrock:InvokeModel` is a value the lab surfaces for
  detection/correlation in step 6.
- `[PROPOSED: aws_ec2_instance_reference]` (proposed) — the launched
  `p4d.24xlarge` instance and the five attempted `p5.48xlarge`
  instances from step 7.
- `[PROPOSED: aws_ec2_key_pair_reference]` (proposed) — the
  `stevan-gpu-key` key pair from step 7.
- `[PROPOSED: aws_security_group_rule_reference]` (proposed) — the
  any-IP all-TCP ingress rule from step 7.
- `[PROPOSED: terraform_module_reference]` (proposed) — identifies the
  `terraform-bedrock-deploy.tf` module described in step 8.
- `[PROPOSED: iam_access_analyzer_findings]` (proposed) — the
  externally-access, internal-access, and unused-access finding lists
  enumerated in step 5.

(Several additional inventory and dump types are referenced in §4 step
outputs; not duplicating the full list here.)

## 8. Defender techniques

This blog is `incident_analysis` shape; defender techniques apply. The
blog names eleven Sysdig Secure detection rules by name under a
"Detection with Sysdig Secure" section; each is reproduced verbatim
below and mapped to the chain step(s) it covers.

- **Name:** "Update Lambda Function Code"
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** step 3
  - **Description:** Alert on `UpdateFunctionCode` CloudTrail events
    against production Lambda functions; the blog identifies this as a
    named Sysdig Secure rule covering the Lambda code-injection pivot.

- **Name:** "Create Access Key for User"
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** steps 3, 4
  - **Description:** Detect `iam:CreateAccessKey` against IAM users —
    applies both to the escalation event in step 3 (where the
    `EC2-init` Lambda creates an access key for a high-privilege user)
    and to step 4 (where the attacker takes over four existing IAM
    users by issuing new access keys).

- **Name:** "Attach Administrator Policy"
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** step 4
  - **Description:** Alert on `iam:AttachUserPolicy` /
    `iam:AttachRolePolicy` events where the attached policy is
    `arn:aws:iam::aws:policy/AdministratorAccess` (the
    `backdoor-admin` event).

- **Name:** "Bedrock Model Recon Activity"
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** steps 2, 6
  - **Description:** Detect reconnaissance activity against Bedrock
    foundation models, custom models, and model-invocation logging
    configuration; covers both the initial enumeration touch on
    Bedrock and the `GetModelInvocationLoggingConfiguration` precheck.

- **Name:** "Create Security Group Rule Allowing Ingress Open to the World"
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** step 7
  - **Description:** Alert on `ec2:AuthorizeSecurityGroupIngress`
    events whose rule permits inbound traffic from `0.0.0.0/0` across
    all ports (the any-IP all-TCP rule the attacker created alongside
    the GPU instance).

- **Name:** "Lateral Movement using Roles for Privilege Escalation"
  - **Technique kind:** `threat_hunting`
  - **Applies to chain steps:** step 4
  - **Description:** Hunt for sessions where a single source principal
    chains multiple `AssumeRole` calls in a short window across
    different roles; also covers failed `AssumeRole` calls targeting
    non-existent or unrelated external accounts as a possible
    LLM-assistance tell.

- **Name:** "High Number of Bedrock Model Invocations"
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** step 6
  - **Description:** Detect anomalous spikes in `bedrock:InvokeModel`
    request rates, including cross-Region invocation patterns; covers
    the 13-invocation LLMjacking event.

- **Name:** "Access Key Enumeration Detected"
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** step 4
  - **Description:** Detect enumeration of IAM access keys across users
    (the precursor reconnaissance that informs which existing users to
    take over by issuing new keys).

- **Name:** "IAM Enumeration Detected"
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** step 2
  - **Description:** Detect high-volume IAM Describe/List/Get
    sequences from a single principal within a short window.

- **Name:** "Lambda Enumeration Detected"
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** step 2
  - **Description:** Detect high-volume Lambda enumeration
    (`ListFunctions`, `GetFunction`, etc.) from a single principal,
    which is the staging activity that precedes the code-injection
    pivot in step 3.

- **Name:** "Organization Enumeration Detected"
  - **Technique kind:** `detection_engineering`
  - **Applies to chain steps:** step 2
  - **Description:** Detect enumeration against the AWS Organizations
    service from credentials that do not legitimately need that
    visibility.

## 9. Defenses

Per `schema.md §4.8` lines 416–426. Reproduced from the blog's
hardening section in order; structurally aligned to the Sysdig
recommendations.

- **Description:** Apply the principle of least privilege to all IAM
  users and roles, including execution roles used by Lambda functions.
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** steps 3, 4, 8
  - **Detection path / format:** Not applicable (preventative).

- **Description:** Restrict `UpdateFunctionConfiguration` and
  `iam:PassRole` permissions carefully — limit them to principals that
  genuinely need them.
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** steps 3, 8
  - **Detection path / format:** Not applicable (preventative).

- **Description:** Limit `lambda:UpdateFunctionCode` permissions to
  specific Lambda functions and assign them only to principals that
  genuinely need code-deployment capabilities.
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** step 3
  - **Detection path / format:** Not applicable (preventative).

- **Description:** Enable Lambda function versioning to maintain
  immutable records of the code running at any point.
  - **Applicability:** `architectural_mitigation`
  - **Addresses chain steps:** step 3
  - **Detection path / format:** Not applicable (architectural).

- **Description:** Ensure S3 buckets containing sensitive data,
  including RAG data and AI model artifacts, are not publicly
  accessible.
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** step 1
  - **Detection path / format:** Not applicable (preventative).

- **Description:** Enable Bedrock model-invocation logging to detect
  unauthorized usage; alert on logging-disable events and on calls to
  `GetModelInvocationLoggingConfiguration` from non-administrator
  principals.
  - **Applicability:** `detection_only`
  - **Addresses chain steps:** step 6
  - **Detection path / format:** Bedrock invocation logs (CloudWatch
    Logs or S3 destination per AWS Bedrock logging configuration).

- **Description:** Monitor for IAM Access Analyzer enumeration, as
  Access Analyzer findings provide threat actors with valuable
  reconnaissance data about the environment (external-access,
  internal-access, and unused-access findings).
  - **Applicability:** `detection_only`
  - **Addresses chain steps:** step 5
  - **Detection path / format:** CloudTrail management events filtered
    on `access-analyzer:List*` and `access-analyzer:Get*` operations.

- **Description:** Prefer IAM roles with temporary credentials over
  long-lived IAM-user access keys; never leave access keys in
  publicly accessible storage; if long-lived credentials must be
  used, secure them and enforce periodic rotation. Blog's verbatim
  recommendation (italicized in source; from the "Sysdig TRT
  mitigation tips" tip box following the S3-credential-theft
  section):
  *"Leaving access keys in public buckets is a huge mistake.
  Organizations should prefer IAM roles instead, which use temporary
  credentials. If they really want to leverage IAM users with
  long-term credentials, they should secure them and implement a
  periodic rotation."*
  - **Applicability:** `customer_actionable`
  - **Addresses chain steps:** step 1
  - **Detection path / format:** Not applicable (preventative).

- **Description:** Monitor for massive cross-region resource
  enumeration by an IAM user or custom role; treat as a suspicious
  pattern requiring investigation. Blog's verbatim recommendation
  (italicized in source; from the tip box following the
  Reconnaissance section):
  *"Massive enumeration of resources across regions performed by an
  IAM user or custom role is usually a suspicious pattern that should
  be monitored by defenders."*
  - **Applicability:** `detection_only`
  - **Addresses chain steps:** step 2
  - **Detection path / format:** CloudTrail management events
    filtered on Describe/List/Get sequences by single principal across
    multiple regions within a short window.

- **Description:** Properly scope and monitor IAM roles that can be
  assumed across accounts; cross-account-assumable roles enable rapid
  lateral movement once compromised. Blog's verbatim recommendation
  (italicized in source; from the tip box following the
  Lateral-movement section):
  *"IAM roles that can be assumed across accounts should be properly
  scoped and monitored. That's because compromising them leads to
  moving laterally among different accounts, creating new, huge
  opportunities for attackers."*
  - **Applicability:** `architectural_mitigation`
  - **Addresses chain steps:** step 4
  - **Detection path / format:** Not applicable (architectural +
    detection-on-misuse).

- **Description:** Implement a Service Control Policy (SCP) at the AWS
  Organizations level restricting `bedrock:InvokeModel` to an explicit
  allowlist of approved models. Blog's verbatim recommendation
  (italicized in source; quoted here without italic formatting):
  *"Invoking Bedrock models that no one in the account uses is a red
  flag. Organizations can create Service Control Policies (SCPs) to
  allow only certain models to be invoked in any member account,
  despite the permissions of the caller. AWS provides an example of
  that policy."* Recommended in the blog's "Sysdig TRT mitigation
  tips" tip box following the LLMjacking-via-Bedrock section, linked
  to an AWS-published aws-samples example.
  - **Applicability:** `architectural_mitigation`
  - **Addresses chain steps:** step 6
  - **Detection path / format:** Not applicable (preventative).
  - **Reference:**
    `https://github.com/aws-samples/service-control-policy-examples/blob/main/Service-specific-controls/Amazon-Bedrock/Deny-Bedrock-model-invocation-except-approved-models.json`

- **Description:** Implement a Service Control Policy (SCP) at the AWS
  Organizations level restricting `ec2:RunInstances` to an explicit
  allowlist of approved instance types. Blog's verbatim recommendation
  (italicized in source; quoted here without italic formatting):
  *"To prevent non-approved EC2 instance types from being launched in
  your AWS organization, you can use this SCP, provided by AWS."*
  Recommended in the blog's "Sysdig TRT mitigation tips" tip box
  following the GPU-instance section, linked to an AWS-published
  aws-samples example.
  - **Applicability:** `architectural_mitigation`
  - **Addresses chain steps:** step 7
  - **Detection path / format:** Not applicable (preventative).
  - **Reference:**
    `https://github.com/aws-samples/service-control-policy-examples/blob/main/Service-specific-controls/Amazon-EC2/Require-Amazon-EC2-instances-to-use-a-specific-type.json`

## 10. External references

- **CVEs cited:** Not stated in blog.
- **Related blogs / Sysdig pages:**
  - LLMjacking definition / category page:
    `https://www.sysdig.com/learn-cloud-native/what-is-llmjacking` —
    anchor text "LLMjacking" — surrounding sentence: *"LLMjacking,
    which was first identified by the Sysdig TRT in May 2024, is an
    attack where the threat actor compromises a principal in the
    victim's cloud account to gain access to cloud-hosted LLMs."*
  - Original LLMjacking blog post:
    `https://www.sysdig.com/blog/llmjacking-stolen-cloud-credentials-used-in-new-ai-attack`
    — anchor text "LLMjacking" — appears in the executive-summary
    paragraph as background for the Bedrock-abuse leg of this campaign.
- **External documentation / examples cited in the blog:**
  - `https://pathfinding.cloud/paths/lambda-004` — Lambda
    privilege-escalation documentation.
  - `https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html`
    — AWS Bedrock cross-Region inference user-guide page (linked in
    the cross-Region-inference sentence in step 6).
  - `https://github.com/aws-samples/service-control-policy-examples/blob/main/Service-specific-controls/Amazon-Bedrock/Deny-Bedrock-model-invocation-except-approved-models.json`
    — AWS-published SCP example for restricting Bedrock model
    invocation (linked from the Bedrock mitigation tip box).
  - `https://github.com/aws-samples/service-control-policy-examples/blob/main/Service-specific-controls/Amazon-EC2/Require-Amazon-EC2-instances-to-use-a-specific-type.json`
    — AWS-published SCP example for restricting EC2 instance types
    (linked from the GPU-instance mitigation tip box).
- **MITRE ATT&CK techniques referenced:** Not stated in blog. The blog
  does not name MITRE technique IDs; the walker has not invented any.

## 11. Real-world incidents

Per `schema.md §4.8` lines 339–356.

- **Status:** `incidents_documented`
- **Evidence source:** The Sysdig blog post itself, written by Sysdig
  Threat Research from CloudTrail-based reconstruction.
- **Incidents:**
  - **Name:** Sysdig-observed AI-assisted AWS intrusion (2025-11-28)
  - **Description:** A real intrusion observed by Sysdig in which an
    attacker stole valid IAM user credentials from a publicly readable
    S3 bucket containing RAG data and progressed through the eight-step
    chain in §4 over a "complete two-hour attack sequence" (the blog's
    phrasing). The headline "admin in 8 minutes" refers specifically to
    the time from initial access to obtaining administrator-level
    credentials in step 3; the remaining seven steps unfolded over the
    rest of the two-hour window.
  - **Affected organizations:** Not named in the blog (typical for
    vendor incident write-ups).
  - **Attribution:** Unattributed campaign. Sysdig argues for
    LLM-assisted operation, citing the hallucinated GitHub repository
    URL (`github.com/anthropic/training-scripts.git`) as evidence
    specifically for LLM code-generation, distinct from the Serbian
    source comments which Sysdig attributes specifically to attacker
    origin. The hallucinated cross-account `AssumeRole` IDs are framed
    as further "potential evidence" but not as definitive LLM tells.
  - **Date range:** 2025-11-28 (explicit in the blog). The timeline
    section reconstructs a complete two-hour sequence from
    `0:00:00` (credential extraction) through `1:51:00` ("The threat
    actor's access was terminated and the attack ended") based on
    CloudTrail analysis. The headline "8 minutes" is bolded in the
    source as: *"The threat actor completed the entire sequence from
    credential theft to successful Lambda execution in just eight
    minutes, including reconnaissance to identify admin users and
    roles."*
  - **Indicators of compromise (IPs, all marked VPN in the blog's IoC
    table):** 103.177.183.165, 104.155.129.177, 104.155.178.59,
    104.197.169.222, 136.113.159.75, 152.58.47.83, 194.127.167.92,
    197.51.170.131, 204.152.223.172, 34.171.37.34, 34.173.176.171,
    34.30.49.235, 34.63.142.34, 34.66.36.38, 34.69.200.125,
    34.9.139.206, 35.188.114.132, 35.192.38.204. Several IPs fall
    inside Google Cloud Platform ranges (34.x.x.x, 35.x.x.x), which
    is consistent with VPN exit nodes hosted on GCP.

## 12. Expected lab class

- **Lab kind:** AWS incident-analysis walkthrough of an LLM-assisted
  privilege-escalation and resource-abuse campaign — a multi-objective
  TTP chain rooted in IAM/Lambda escalation, branching into Bedrock
  LLMjacking, GPU resource abuse, and a staged-but-unrealized IaC
  backdoor, with explicit detection signatures per phase.
- **Why this class:** The §6 facets concentrate on AWS identity,
  compute, and Bedrock services (`target:aws_iam`, `target:aws_lambda`,
  `target:aws_bedrock`, `target:aws_ec2`, `target:aws_organizations`,
  `target:aws_iam_access_analyzer`) with a single first-class runtime
  (`runtime:aws`), and the lab_class_signal facets emphasise
  infrastructure provisioning, world-state production, expected
  detections (eleven named Sysdig rules), and a manual
  Bedrock-enablement prereq. Combined with the `incident_analysis`
  shape and the per-step detection guidance, the Planner should
  classify this as an AWS incident-analysis lab with multiple
  persistent-resource phases and a manual prereq for the Bedrock leg.

## 13. Reproducibility (lab-level)

Derived from the per-step `reproducibility` values in §4 via the
any-heterogeneity-mixed rule (`schema.md §4.8` line 438).

- **Classification (lab-level):** `mixed`
- **Caveats:**
  - Three steps are `full` (2, 3, 4) — the core enumeration plus
    privilege-escalation chain plus lateral movement reproduces
    end-to-end.
  - Four steps are `partial_simulation` (1, 5, 6, 8) — these involve
    lab-seeded mock data (creds in S3, exfil targets) or service
    limitations (Bedrock model availability) or divergence from the
    observed real-world incident (Terraform module was staged but
    never deployed in the incident; the lab will go further).
  - One step is `demonstration_only` (7) — the GPU launch sequence is
    illustrated rather than executed due to cost and quota.
- **Derivation trace:**
  - step 1: `partial_simulation`
  - step 2: `full`
  - step 3: `full`
  - step 4: `full`
  - step 5: `partial_simulation`
  - step 6: `partial_simulation`
  - step 7: `demonstration_only`
  - step 8: `partial_simulation`
  - Set: `{full, partial_simulation, demonstration_only}` — three
    distinct tiers → `mixed`.
- **Overall assessment:** A faithful reproduction of the central
  enumeration → escalation → lateral-movement chain (steps 2–4) plus
  structurally accurate but data-substituted reproductions of
  exfiltration, LLMjacking, and IaC-backdoor staging steps, with the
  GPU-launch step demonstrated rather than executed.

## 14. Coverage tags

Drives the `coverage_tags:` field in `eval/blog-sets/manifest.yaml`.

- `cloud:aws`
- `complexity:medium` — 8 chain steps (in the 4–8 band per `eval.md §7.3`)
- `thesis:ttp_chain`
- `thesis:incident_analysis`
- `thesis:privilege_escalation`
- `lab_class_signal:incident_analysis`
- `lab_class_signal:requires_infra`
- `lab_class_signal:produces_world_state`
- `lab_class_signal:expected_detections`
- `lab_class_signal:manual_prereq`
- `incident_analysis`

Not applied: `multi_platform` (single cloud), `vulnerability_disclosure`
(no CVE / no vulnerability story), `non_first_class_runtime`
(`runtime:aws` is first-class).

## 15. Manual ground-truth notes

**Drafted by:** Claude Opus 4.7, 2026-05-20. Revised four times on
2026-05-20 — (1) after reviewer-flagged paraphrase corrections; (2)
after a second reviewer pass that caught residual factual errors
(notably: the escalation target user `frick` IS named in the blog
despite an earlier walker note claiming otherwise; the two SCP
recommendations ARE in the blog in "Sysdig TRT mitigation tips" tip
boxes despite an earlier walker note claiming they were
walker-additions; the "In some cases" qualifier on cross-Region
inference was dropped from an excerpt; the instance-termination
excerpt was paraphrased to passive voice; and two LLMjacking-related
URLs and two aws-samples SCP URLs were captured rather than punted to
the Extractor); (3) after a third reviewer pass that caught a
fabricated "verbatim" quote attributed to the blog's Bedrock SCP tip
box (the EC2 SCP tip's wording had been parallel-structured into the
Bedrock entry by a WebFetch pass; replaced with the actual Bedrock
tip text); and (4) after a fourth, walker-driven verification pass
using raw-HTML grep (bypassing model-mediated WebFetch processing
entirely) that surfaced previously-missed content: three additional
"Sysdig TRT mitigation tips" tip boxes (S3 credentials, enumeration
monitoring, cross-account role hygiene), the bolded "intentionally
created for automation" sentence in step 1 that vindicates an
original first-draft paraphrase I had wrongly rejected as
hallucinated, the literal IAM user identifier `compromised_user`,
the named user `rocker` (BedrockFullAccess), specific assumed and
failed role names, the Lambda timeout change (3 → 30 seconds), the
`AcceptAgreementRequest` Marketplace call for Claude models, the
within-source "In some cases" vs "throughout" contradiction about
cross-Region inference scope, the 18 IoC IPs, and the 1:51:00
attack-end timestamp. Verified by: [pending human review].

**Source-handling note:** Initial draft excerpts were sourced from a
sub-agent's read of the blog and contained multiple paraphrases that
diverged from the literal blog text. The parent agent re-fetched the
blog directly across four verification rounds — three via WebFetch
and one via raw-HTML curl + grep — and replaced every excerpt with
verified verbatim text. All §4 and §9 quoted excerpts now correspond
to literal sentences in the source. Worth recording for the next
walker: WebFetch (which uses an LLM to summarize HTML into prose) is
**not reliable** for verbatim or exhaustive extraction. It produced
three categories of error in this walk: (a) paraphrase-as-verbatim
in Round 1; (b) missing one of two LLMjacking URLs in Round 2; (c)
parallel-structure fabrication of the Bedrock SCP tip in Round 3.
The Round 4 grep pass on raw HTML revealed substantial
content-density gaps WebFetch had not surfaced — three of five tip
boxes missing, an entire timeline section's worth of named
entities, and the IoC table. **Lesson: for any walk that claims
"verbatim," a raw-HTML byte-level pass (curl + grep with
substring-containment tests) is mandatory, not optional. Model-
mediated fetches are a starting point, not a verification.**

### Ambiguous wording in the blog

- **"likely suggesting the threat actor's origin."** (re: Serbian
  comments in the injected Lambda code) — the blog uses Serbian
  comments only as a signal of origin, not as a signal of LLM
  generation. Sysdig's LLM-generation argument rests on a different
  signal (the hallucinated `github.com/anthropic/training-scripts.git`
  URL in step 8). An LLM Extractor may collapse these two distinct
  arguments into a single "LLM tells" cluster and misattribute the
  reasoning.
- **The third hallucinated account ID (`653711XXXXXX`)** — the blog
  says this one "may belong to a real external account," distinguishing
  it from the two clearly pattern-based hallucinated IDs
  (`123456789012`, `210987654321`). Walker treated all three as
  attempted (real) cross-account `AssumeRole` calls, not as
  illustrative examples; the blog uses `XXXXXX` as a redaction rather
  than as a fabrication marker.
- **"After verifying that logging was disabled"** — the construction
  implies the logging state was *already* disabled before the attacker
  arrived and the attacker only verified it; not that the attacker
  disabled it. Walker treated the disabled state as a step 6
  precondition (ambient account state), not as an attacker action.

### Missing information

- **MITRE technique IDs.** None are given. The walker did not invent
  IDs per ground rule 8.
- **Per-step timeline.** The blog mentions a "complete two-hour attack
  sequence" and the "8 minutes" admin-access subhead. The timeline
  section gives partial granularity (e.g. "0:58:00-1:51:00" for the
  Bedrock invocation window) but not every step is anchored. An LLM
  Extractor will likely anchor `duration_as_described` to "8 minutes"
  and miss the two-hour total.

### Alternative readings

- **Step 8 (Terraform backdoor) as alternative path vs canonical
  step.** The blog narrates the staged Terraform module inline with the
  rest of the campaign but explicitly says it was never deployed. A
  defensible alternative is to put it in §5 (alternative paths) as an
  attempted-but-unrealized branch rather than as canonical step 8.
  Walker chose canonical step 8 because the blog narrates it as part
  of the same incident sequence; LLM Extractor might prefer the §5
  reading.
- **Step 3 as one step vs three.** The blog notes the attacker
  iterated `UpdateFunctionCode` three times before the payload worked.
  Walker collapsed this into a single chain step; the three iterations
  could be split out if the eval cares about the "AI generated buggy
  code, retried" signal.
- **Step 7 reproducibility tier.** Walker chose `demonstration_only`
  because actually running `p4d.24xlarge` is gated by quota and
  expensive. A defensible alternative is `not_reproducible` (drop the
  step) if the lab cannot meaningfully demonstrate the technique
  without running it; walker rejected that reading because the API
  shape, the `Insufficient capacity` failure mode for `p5.48xlarge`,
  the security-group rule shape, the key-pair creation, and the
  would-be cost are all worth demonstrating.

### Calls the walker made

- **Shape: `incident_analysis`, not `aws_ttp`.** This is the most
  consequential judgment call. Sysdig narrates a campaign retrospective
  with eleven named Sysdig Secure detection rules and explicit
  hardening guidance, which keeps §8 (Defender techniques) active. A
  pure-`aws_ttp` reading would drop §8 and treat the blog as a
  knowledge piece.
- **Bedrock model facet granularity.** Walker proposed
  `target:aws_bedrock` rather than a more specific facet per model;
  Phase 1+ facet evolution may want a finer split.
- **`aws_temporary_credentials` vs `aws_credentials` distinction.** The
  blog frequently writes "credentials" without distinguishing
  long-lived IAM user credentials from STS session credentials. Walker
  separated these into two value types because the schema for the two
  is genuinely different (session credentials include a session token
  and expiry). Extractor will probably miss this distinction.
- **Lab-level `mixed`, not `partial_simulation`.** The any-heterogeneity
  rule is the architecturally correct call per `schema.md §4.8` line
  438, but a weakest-tier-wins rule would yield `demonstration_only`.
  Walker followed the documented rule.
- **One Sysdig-rule entry per chain step it covers.** Where a single
  Sysdig rule maps to multiple chain steps (e.g., "Create Access Key
  for User" applies to both steps 3 and 4), walker listed it once and
  noted both applications, rather than duplicating the rule.

### Failure modes to watch (for an LLM Extractor on the same blog)

- **Conflating "what Sysdig detected" with "what the attacker did."**
  The blog mixes attacker narrative with defender narrative in close
  paragraphs. An Extractor may attribute defender capabilities to the
  attacker (e.g., treating "Sysdig observed AssumeRole chains" as a
  step the attacker took rather than a detection the defender made).
- **Counting Bedrock models vs invocations.** The blog says "13 times"
  but lists 9 distinct models. An Extractor that conflates these two
  numbers (or that collapses 9 specific model IDs into 5 model
  "families") will undercount one and over-collapse the other. (This is
  the exact error the walker's first-draft contained and the reviewer
  flagged.)
- **The "AI-assisted" framing.** Sysdig's argument for LLM assistance
  has two distinct evidence chains — (a) Serbian source comments →
  attacker origin, (b) hallucinated GitHub URL → LLM-generated code —
  plus a weaker third signal (hallucinated cross-account IDs). An
  Extractor may either over-weight any of these into a hard
  attribution, or drop them entirely. The walker has kept them in
  §11 (attribution field) and §15 with their proper attribution split.
- **The five-IAM-users math.** The blog states "five IAM users" and
  separately that "Four of the five compromised IAM users already
  existed" plus the creation of `backdoor-admin`. An Extractor may
  miscount this as 5 existing + 1 new = 6, when the correct reading is
  4 existing + 1 new = 5. (This is the exact error the walker's
  first-draft contained.)
- **"Disabled by attacker" vs "already disabled."** The Bedrock-logging
  precondition reads as already-disabled-account-state, not as an
  attacker action. An Extractor may invent a "disabled Bedrock logging"
  attacker step.
- **Step 3's "three iterations."** Easy to miss as a separate finding
  worth structuring; the blog mentions it in one clause.
- **Step 7 success vs attempt.** The blog distinguishes failed
  `p5.48xlarge` attempts from the successful `p4d.24xlarge` launch. An
  Extractor that aggregates "the attacker tried to launch GPUs" loses
  the failure-mode signal that informs why the attacker downgraded.
- **Walker un-doing a correct first read.** The first-draft Explore
  agent correctly extracted the admin user `frick` from the blog;
  walker then revised the walk and asserted in §15 that the user's
  name was *not* in the blog — undoing a correct extraction. Lesson:
  when revising in response to a reviewer flag, re-check that
  corrections don't unwind previously-correct facts. Applies more
  broadly than to user names.
- **Tip boxes vs main mitigation list.** The blog has both a
  consolidated "Mitigation recommendations" list AND service-specific
  "Sysdig TRT mitigation tips" tip boxes interleaved with the
  narrative. An Extractor that only scans the consolidated list will
  miss the two SCP recommendations (Bedrock, EC2) which appear only in
  the tip boxes.
- **Qualifier-stripping.** The cross-Region inference sentence opens
  with "In some cases" — meaning fewer than 13 invocations used the
  feature. A walker (or Extractor) that strips the qualifier turns "in
  some cases" into "in all cases," which is a factual upgrade. Watch
  for any sentence beginning with a frequency / scope qualifier
  ("Sometimes," "Occasionally," "In some cases," "Often," "Generally")
  and preserve it verbatim.
- **Parallel-structure fabrication.** When the blog has two adjacent
  similar artifacts (e.g., two "Sysdig TRT mitigation tips" tip boxes
  — one for Bedrock SCPs, one for EC2 SCPs), a WebFetch-style read can
  silently synthesize one of them by parallel-structuring it against
  the other's actual wording. Concrete instance from this walk: the
  EC2 SCP tip-box actually reads *"To prevent non-approved EC2
  instance types from being launched in your AWS organization, you
  can use this SCP, provided by AWS"* — verbatim. The Bedrock SCP tip
  box reads *"Invoking Bedrock models that no one in the account uses
  is a red flag. Organizations can create Service Control Policies
  (SCPs) to allow only certain models to be invoked in any member
  account, despite the permissions of the caller. AWS provides an
  example of that policy"* — completely different wording. An earlier
  WebFetch pass returned the Bedrock tip in EC2-parallel form ("To
  prevent unauthorized Bedrock model invocation in your AWS
  organization, you can use this SCP, provided by AWS"), which is a
  fabrication. Lesson for future verification: when two artifacts are
  reported back with suspiciously similar template-shape phrasing,
  re-fetch each one individually with a substring-containment test
  ("does the exact string X appear in the page?") rather than trusting
  a side-by-side extraction.
- **Within-source contradiction.** The blog itself contains two
  inconsistent statements about cross-Region inference scope: the
  main-narrative sentence in step 6 begins *"In some cases, they
  leveraged cross-Region inference…"* while the timeline-section
  bullet for 0:58:00 – 1:51:00 states *"They leveraged cross-Region
  inference throughout."* The walker preserved both verbatim and
  flagged the inconsistency in step 6's description rather than
  picking one — an LLM Extractor faced with the same blog should
  surface contradictions rather than silently resolving them.
- **Timeline section is information-dense and easy to skip.** The
  "Attack timeline" h3 near the end of the blog contains
  CloudTrail-derived per-step timestamps and several facts that do
  not appear elsewhere in the body: the literal IAM user identifier
  `compromised_user`; the Lambda timeout change from 3 to 30 seconds;
  the named user `rocker` (which had the `BedrockFullAccess` policy);
  the specific assumed roles (`sysadmin`, `netadmin`, `account`,
  `developer`, `external`) and failed-assume targets (`admin`,
  `Administrator`, `EKS-access`); the `AcceptAgreementRequest`
  Marketplace API call required to invoke certain Claude models;
  and the "1:51:00 — The threat actor's access was terminated and
  the attack ended" close-out. An Extractor that processes only the
  prose body and treats the timeline as a summary table will miss all
  of these.
- **IoC capture (IPs and VPN status).** The blog has a small IoC
  table with ~18 IPs, all marked as VPN exits. Several are in GCP
  IP ranges. The walk captured these in §11; an Extractor should
  treat the IoC table as a first-class output, not optional context.
- **Multiple "Sysdig TRT mitigation tips" tip boxes.** The blog has
  FIVE tip boxes interleaved with the narrative (after S3-credential
  theft, after reconnaissance, after lateral-movement, after the
  Bedrock-LLMjacking section, after the GPU-instance section) PLUS a
  consolidated "Mitigation recommendations" list at the end. An
  Extractor that only scans the consolidated list will miss the five
  service-specific tip-box recommendations. The walk's §9 now
  contains all eight defenses sourced from blog text (seven from the
  tip boxes plus one from the consolidated list, with overlap noted
  where the same recommendation appears in both forms).

### Phase 1 prompt-engineering hints

- The Extractor prompt should explicitly tell the model: detection
  signatures the vendor names are *defender techniques* (§8), not
  attacker chain steps (§4) — even when they appear in the same
  paragraph as the attacker action. The eleven Sysdig rule names in
  this blog are a sharp test case.
- The Extractor prompt should ask the model to distinguish between
  "credentials" (long-lived) and "session credentials" (STS) and pick
  the value-type accordingly.
- For multi-objective campaigns like this one, the prompt should
  encourage the model to keep distinct objectives as distinct steps
  (LLMjacking ≠ exfiltration ≠ GPU resource abuse ≠ IaC backdoor)
  rather than collapsing them into a single "monetization" step.
- When a blog narrates attempted-but-failed actions (the five
  `p5.48xlarge` attempts) or staged-but-not-deployed actions (the
  Terraform module), the prompt should ask the model to record them as
  canonical chain steps with `partial_simulation` or
  `demonstration_only` tiers rather than silently dropping them.
- When a blog reports counts ("nine models", "13 invocations", "five
  IAM users", "four already existed"), the prompt should ask the model
  to extract every count separately and to derive arithmetic
  relationships rather than collapsing counts into a single number.
- When a blog cites multiple distinct LLM-tell signals (Serbian
  comments → origin, hallucinated URL → LLM-generation), the prompt
  should preserve the attribution chain — which signal supports which
  inference — rather than clustering them into a generic "LLM-assisted"
  bucket.
