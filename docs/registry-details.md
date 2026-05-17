# cyberlab-gen — Registry Details

**Companion to:** `architecture.md` (hub), `schema.md §4.11–§4.16` (registries architectural layer), `schema-details.md` (Pydantic shapes for registry meta-schemas).
**Document scope:** The v1 seed contents of every registry that ships with the cyberlab-gen distribution — `value_types`, `facets`, `external_data_sources`, `static_catalogs`, `execution_contexts`, plus the closed bundled-only catalogs (`detection_components`, `severity_levels`, `detection_formats`, `provisioning_mechanisms`, `lab_credentials`, `thesis_types`). For each registry: the entry shape (canonical example), the v1 entries, and notes on what gets added at runtime versus what stays maintainer-curated.

This document is implementation-ready. The YAML examples here are what gets checked into `registry/` in the repo. Where a registry is open-set and grows by runtime proposal, the v1 seed represents *what ships*, not the universe of valid entries.

---

## 1. Reading guide

Three categories of registry behavior, repeated throughout this document:

- **Open-set, runtime-extensible.** Agents may propose new entries via the proposal lifecycle in `schema.md §4.16`. v1 seed entries are starting points; the user overlay accumulates more over time. `value_types`, `facets` (per-category), `execution_contexts`.
- **Closed-set, maintainer-only.** Entries are added by PR to the cyberlab-gen repo only. No runtime proposals. Used for stable industry-standard categories (detection components, severity) or for entries where adding requires code changes (external data sources, provisioning mechanisms). `external_data_sources`, `static_catalogs`, `detection_components`, `severity_levels`, `detection_formats`, `provisioning_mechanisms`, `lab_credentials`.
- **Open-set in spirit but no runtime proposal flow.** The registry grows by maintainer PR (informed by telemetry-aggregated patterns from `eval.md §7.9`), not by agent proposal at runtime. `thesis_types` falls here — the v1 seed is the curated walk's enumeration; promotion of new thesis types happens via PR.

Two of these categories overlap on the "static reference data" property: `static_catalogs` (closed-set) and `lab_credentials` (closed-set) are *consulted* on-demand by agents and validators, not iterated through. They're listed under closed-set above (which describes their proposal-flow status) rather than carving out a fourth category for their consultation pattern.

Each section below states the category and the entry shape, then lists the v1 entries.

For every registry, **every entry's keys conform to the `SnakeName` convention** from `schema-details.md §2.1`: lowercase, snake-case, alphanumeric plus underscore, no leading digit. Facet names use `category:value` shape (e.g., `target:aws`) where the prefix is one of the closed category enums and the value follows `SnakeName`.

---

## 2. `value_types` registry

**Path:** `registry/value_types.yaml`
**Category:** Open-set, runtime-extensible. The Extractor proposes new entries via `propose_value_type` per `schema.md §4.16`.
**Proposal authority:** Extractor only (blog-derived).
**Architectural reference:** `schema.md §4.12`.

### 2.1 Entry shape

Per `schema.md §4.12`. The Pydantic model is `ValueTypeRegistryEntry` from `schema-details.md`; the YAML shape is:

```yaml
- name: aws_credentials                       # SnakeName, registry key
  description: "Long-lived AWS access key plus secret access key pair."
  schema:                                     # JSON Schema for the value's shape
    type: object
    required: [access_key_id, secret_access_key]
    properties:
      access_key_id: { type: string, pattern: "^(AKIA|ASIA)[0-9A-Z]{16}$" }
      secret_access_key: { type: string, minLength: 40 }
      session_token: { type: string, nullable: true }
  sensitive: true
  examples:
    - access_key_id: "AKIAIOSFODNN7EXAMPLE"
      secret_access_key: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
  notes_for_generator: |
    The credential pair is the most common AWS-style identity material.
    When this type is in a phase's `produces_world_state`, the cleanup
    must include `aws iam delete-access-key` before the user can be deleted.
    Treat as `sensitive=true` in any Terraform output.
  cleanup_metadata: |
    Access keys are deleted via `aws iam delete-access-key
    --user-name <user> --access-key-id <id>`. Must be deleted before
    the parent IAM user can be deleted.
  platforms: [aws]
  proposed_by: maintainer                     # maintainer for bundled; extractor for runtime-proposed; planner not valid here
  proposed_in_run: null                       # null for bundled entries; a run-id for overlay entries
```

For bundled entries `proposed_by` is `maintainer` (or omitted — that's the default) and `proposed_in_run` is `null` (or omitted). For overlay (runtime-proposed) entries the framework sets `proposed_by: extractor` (or `planner`) and `proposed_in_run: <run-id>` when the proposal is accepted.

The full proposal envelope — `proposal_origin`, `source_lab`, `source_blog`, `proposed_by_model`, `proposed_at`, `reasoning` — is preserved as audit context in the overlay file under a separate top-level `proposals:` block keyed by entry name, per `schema.md §4.16`. The registry-entry shape itself is identical between bundled and overlay (so the YAML examples here apply to both); the difference is at the registry-file level, where overlay files have an additional `proposals:` block. See `schema-details.md §6.6` for the Pydantic shape (`OverlayRegistryFile[E]` and `ProposalAuditBlock`).

### 2.2 v1 seed entries

The v1 seed covers value types that recur across the four first-class platforms (AWS, Azure including Entra ID, GCP, GitHub) plus the package-ecosystem and generic types that show up in supply-chain blogs. Entries are grouped here for readability; the YAML file is a flat list.

**Coverage approach.** The seed below targets ~80 entries spanning the categories illustrated in `schema.md §4.12.575–4.12.582`. Coverage is not exhaustive — the registry is designed to grow via overlay (`schema.md §4.16`). Categories where one obvious entry per platform suffices (e.g., `*_role_arn`) get one entry per platform; categories where shapes diverge meaningfully (e.g., credential types) get one entry per shape.

#### 2.2.1 AWS — credentials and identity

```yaml
- name: aws_credentials
  description: "Long-lived AWS access key plus secret access key (IAM user credentials)."
  schema:
    type: object
    required: [access_key_id, secret_access_key]
    properties:
      access_key_id: { type: string, pattern: "^AKIA[0-9A-Z]{16}$" }
      secret_access_key: { type: string, minLength: 40 }
  sensitive: true
  examples:
    - { access_key_id: "AKIAIOSFODNN7EXAMPLE", secret_access_key: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" }
  notes_for_generator: |
    Long-lived; the common form of "leaked AWS key" in supply-chain blogs.
    Distinct from aws_temporary_credentials (which includes session token).
  platforms: [aws]

- name: aws_temporary_credentials
  description: "STS-issued temporary AWS credentials with session token (e.g., from AssumeRole)."
  schema:
    type: object
    required: [access_key_id, secret_access_key, session_token]
    properties:
      access_key_id: { type: string, pattern: "^ASIA[0-9A-Z]{16}$" }
      secret_access_key: { type: string, minLength: 40 }
      session_token: { type: string }
      expiration: { type: string, format: date-time }
  sensitive: true
  notes_for_generator: |
    Session-scoped; expires. When a phase produces these, downstream phases
    must complete before expiration. Cleanup is implicit (expiration); no
    explicit deletion command required.
  platforms: [aws]

- name: aws_iam_user_arn
  description: "Fully-qualified ARN of an IAM user."
  schema:
    type: string
    pattern: "^arn:aws:iam::[0-9]{12}:user/.+$"
  sensitive: false
  examples: ["arn:aws:iam::123456789012:user/lab-victim"]
  platforms: [aws]

- name: aws_iam_role_arn
  description: "Fully-qualified ARN of an IAM role."
  schema:
    type: string
    pattern: "^arn:aws:iam::[0-9]{12}:role/.+$"
  sensitive: false
  examples: ["arn:aws:iam::123456789012:role/lab-attacker-role"]
  platforms: [aws]

- name: aws_account_id
  description: "12-digit AWS account identifier."
  schema:
    type: string
    pattern: "^[0-9]{12}$"
  sensitive: false
  examples: ["123456789012"]
  platforms: [aws]

- name: aws_region
  description: "AWS region identifier."
  schema:
    type: string
    pattern: "^[a-z]{2}-[a-z]+-[0-9]+$"
  sensitive: false
  examples: ["us-east-1", "eu-west-2"]
  platforms: [aws]

- name: aws_assumed_role_arn
  description: "ARN of an assumed role session (the principal form, distinct from the role ARN)."
  schema:
    type: string
    pattern: "^arn:aws:sts::[0-9]{12}:assumed-role/.+/.+$"
  sensitive: false
  examples: ["arn:aws:sts::123456789012:assumed-role/lab-attacker-role/lab-session"]
  platforms: [aws]
```

#### 2.2.2 AWS — resource references

```yaml
- name: aws_s3_bucket_reference
  description: "S3 bucket name plus region."
  schema:
    type: object
    required: [bucket_name, region]
    properties:
      bucket_name: { type: string, pattern: "^[a-z0-9.\\-]{3,63}$" }
      region: { type: string }
  sensitive: false
  notes_for_generator: |
    Bucket names are globally unique across AWS. Lab buckets should embed
    a deterministic-but-unique suffix (e.g., the lab id) to avoid collisions
    across user runs. Generator should derive from a hash of (lab_id, user_id)
    rather than fixed names.
  cleanup_metadata: |
    Must be emptied before deletion: `aws s3 rm --recursive` then `aws s3 rb`.
    If bucket has versioning enabled, delete versioned objects too.
  platforms: [aws]

- name: aws_s3_object_listing
  description: "Output of an S3 ListObjects call — array of object keys with metadata."
  schema:
    type: array
    items:
      type: object
      properties:
        key: { type: string }
        size: { type: integer }
        last_modified: { type: string, format: date-time }
  sensitive: false
  notes_for_generator: |
    Typical output of a discovery phase. Downstream phases consume the keys
    to decide which object to exfiltrate or modify.
  platforms: [aws]

- name: aws_lambda_function_reference
  description: "Lambda function ARN plus region."
  schema:
    type: object
    required: [function_arn, region]
    properties:
      function_arn: { type: string, pattern: "^arn:aws:lambda:" }
      region: { type: string }
  sensitive: false
  platforms: [aws]

- name: aws_ec2_instance_reference
  description: "EC2 instance ID, region, optional public IP."
  schema:
    type: object
    required: [instance_id, region]
    properties:
      instance_id: { type: string, pattern: "^i-[0-9a-f]{8,17}$" }
      region: { type: string }
      public_ip: { type: string, nullable: true }
  sensitive: false
  platforms: [aws]

- name: aws_security_group_reference
  description: "Security group ID and region."
  schema:
    type: object
    required: [group_id, region]
    properties:
      group_id: { type: string, pattern: "^sg-[0-9a-f]+$" }
      region: { type: string }
  sensitive: false
  platforms: [aws]

- name: aws_imds_token
  description: "EC2 IMDSv2 session token."
  schema:
    type: string
    minLength: 32
  sensitive: true
  notes_for_generator: |
    Obtained via PUT to /latest/api/token with X-aws-ec2-metadata-token-ttl-seconds.
    Short-lived (TTL up to 6 hours). Used as Authorization for subsequent IMDS calls.
  platforms: [aws]

- name: aws_iam_policy_document
  description: "An IAM policy as a JSON document."
  schema:
    type: object
    required: [Version, Statement]
    properties:
      Version: { type: string }
      Statement: { type: array }
  sensitive: false
  notes_for_generator: |
    Used both as input (the policy being attached) and as output (the policy
    being inspected during reconnaissance). Avoid Resource:"*" Action:"*" outside
    of attack_target resources — Layer 3 will flag them as security findings
    even though they may be intentional for the lab.
  platforms: [aws]

- name: aws_secrets_manager_secret_reference
  description: "Secrets Manager secret ARN plus region."
  schema:
    type: object
    required: [secret_arn, region]
    properties:
      secret_arn: { type: string, pattern: "^arn:aws:secretsmanager:" }
      region: { type: string }
  sensitive: false
  platforms: [aws]

- name: aws_kms_key_reference
  description: "KMS key ARN plus region."
  schema:
    type: object
    required: [key_arn, region]
    properties:
      key_arn: { type: string, pattern: "^arn:aws:kms:" }
      region: { type: string }
  sensitive: false
  platforms: [aws]
```

#### 2.2.3 Azure — credentials, identity, Entra ID

```yaml
- name: azure_service_principal_credentials
  description: "Azure service principal credentials (app ID, secret, tenant)."
  schema:
    type: object
    required: [app_id, tenant_id, client_secret]
    properties:
      app_id: { type: string, format: uuid }
      tenant_id: { type: string, format: uuid }
      client_secret: { type: string }
  sensitive: true
  notes_for_generator: |
    The classic Azure CLI/SDK service-principal triple. Distinct from
    managed identity (no secret) and from user credentials.
  platforms: [azure]

- name: azure_managed_identity_reference
  description: "Reference to a managed identity (system-assigned or user-assigned)."
  schema:
    type: object
    required: [principal_id]
    properties:
      principal_id: { type: string, format: uuid }
      client_id: { type: string, format: uuid, nullable: true }
      resource_id: { type: string, nullable: true }
  sensitive: false
  notes_for_generator: |
    Managed identities have no client secret. The principal_id is what
    appears in audit logs. Lab provisioning of managed identities uses
    azurerm_user_assigned_identity or system_assigned blocks on resources.
  platforms: [azure]

- name: entra_user_object_id
  description: "Entra ID (Azure AD) user object ID (GUID, not UPN)."
  schema:
    type: string
    format: uuid
  sensitive: false
  platforms: [azure, entra_id]

- name: entra_service_principal_id
  description: "Entra ID service principal object ID."
  schema:
    type: string
    format: uuid
  sensitive: false
  platforms: [azure, entra_id]

- name: entra_app_registration_id
  description: "Entra ID application registration ID (the app object, distinct from service principal)."
  schema:
    type: string
    format: uuid
  sensitive: false
  notes_for_generator: |
    The "application" object is distinct from the "service principal" object in Entra ID.
    Apps are the registration; service principals are instantiations per tenant.
  platforms: [azure, entra_id]

- name: entra_actor_token
  description: "Entra ID Actor Token (impersonation token from on-behalf-of flow)."
  schema:
    type: string
    pattern: "^eyJ"
  sensitive: true
  notes_for_generator: |
    JWT shape. The actor_token claim chain reveals the impersonating identity.
    Used in Entra impersonation chains (e.g., dirk-jan-mollema research).
  platforms: [azure, entra_id]

- name: entra_impersonation_token
  description: "Generic JWT obtained via Entra ID impersonation primitives."
  schema:
    type: string
    pattern: "^eyJ"
  sensitive: true
  platforms: [azure, entra_id]

- name: azure_subscription_id
  description: "Azure subscription identifier (GUID)."
  schema:
    type: string
    format: uuid
  sensitive: false
  platforms: [azure]

- name: azure_tenant_id
  description: "Entra ID / Azure AD tenant identifier (GUID)."
  schema:
    type: string
    format: uuid
  sensitive: false
  platforms: [azure, entra_id]

- name: azure_resource_id
  description: "Fully-qualified Azure resource ID."
  schema:
    type: string
    pattern: "^/subscriptions/[0-9a-f-]+/resourceGroups/.+"
  sensitive: false
  platforms: [azure]

- name: azure_keyvault_reference
  description: "Key Vault URI and vault name."
  schema:
    type: object
    required: [vault_uri, vault_name]
    properties:
      vault_uri: { type: string, format: uri }
      vault_name: { type: string }
  sensitive: false
  platforms: [azure]

- name: azure_storage_account_reference
  description: "Storage account name and resource group."
  schema:
    type: object
    required: [account_name, resource_group]
    properties:
      account_name: { type: string }
      resource_group: { type: string }
  sensitive: false
  platforms: [azure]

- name: azure_vm_reference
  description: "VM name, resource group, optional public IP."
  schema:
    type: object
    required: [vm_name, resource_group]
    properties:
      vm_name: { type: string }
      resource_group: { type: string }
      public_ip: { type: string, nullable: true }
  sensitive: false
  platforms: [azure]

- name: azure_resource_group_reference
  description: "Resource group name plus location."
  schema:
    type: object
    required: [name, location]
    properties:
      name: { type: string }
      location: { type: string }
  sensitive: false
  platforms: [azure]
```

#### 2.2.4 GCP — credentials, identity, resources

```yaml
- name: gcp_service_account_key
  description: "GCP service account JSON key (long-lived credentials)."
  schema:
    type: object
    required: [type, project_id, private_key_id, private_key, client_email]
    properties:
      type: { type: string, const: "service_account" }
      project_id: { type: string }
      private_key_id: { type: string }
      private_key: { type: string }
      client_email: { type: string, format: email }
  sensitive: true
  notes_for_generator: |
    The classic JSON key download. Long-lived; the cleanup must call
    `gcloud iam service-accounts keys delete`. Workload Identity Federation
    is the modern alternative; lab generator should prefer it when blog allows.
  platforms: [gcp]

- name: gcp_service_account_email
  description: "GCP service account email (the principal identifier)."
  schema:
    type: string
    pattern: ".+@.+\\.iam\\.gserviceaccount\\.com$"
  sensitive: false
  examples: ["lab-attacker@project-id.iam.gserviceaccount.com"]
  platforms: [gcp]

- name: gcp_oauth_access_token
  description: "Short-lived OAuth 2.0 access token (e.g., from gcloud or metadata server)."
  schema:
    type: string
    minLength: 32
  sensitive: true
  notes_for_generator: |
    Obtained via metadata server (http://metadata.google.internal/) or token
    exchange. Short-lived (1 hour typical). No explicit cleanup; expiration handles it.
  platforms: [gcp]

- name: gcp_project_id
  description: "GCP project identifier."
  schema:
    type: string
    pattern: "^[a-z][a-z0-9-]{4,28}[a-z0-9]$"
  sensitive: false
  examples: ["my-lab-project-123456"]
  platforms: [gcp]

- name: gcp_workload_identity_pool_id
  description: "Workload Identity Federation pool identifier."
  schema:
    type: string
  sensitive: false
  platforms: [gcp]

- name: gcp_cloud_run_service_reference
  description: "Cloud Run service name, region, project."
  schema:
    type: object
    required: [service_name, region, project_id]
    properties:
      service_name: { type: string }
      region: { type: string }
      project_id: { type: string }
  sensitive: false
  platforms: [gcp]

- name: gcp_cloud_run_revision_reference
  description: "Specific Cloud Run revision (immutable; used in pin-then-replay attacks)."
  schema:
    type: object
    required: [service_name, revision_name, region, project_id]
    properties:
      service_name: { type: string }
      revision_name: { type: string }
      region: { type: string }
      project_id: { type: string }
  sensitive: false
  platforms: [gcp]

- name: gcp_storage_bucket_reference
  description: "GCS bucket name plus location."
  schema:
    type: object
    required: [bucket_name, location]
    properties:
      bucket_name: { type: string }
      location: { type: string }
  sensitive: false
  platforms: [gcp]

- name: gcp_secret_reference
  description: "Secret Manager secret ID plus project."
  schema:
    type: object
    required: [secret_id, project_id]
    properties:
      secret_id: { type: string }
      project_id: { type: string }
  sensitive: false
  platforms: [gcp]

- name: gcp_iam_role_reference
  description: "IAM role identifier (predefined or custom)."
  schema:
    type: string
    pattern: "^(roles/|projects/.+/roles/).+"
  sensitive: false
  examples: ["roles/owner", "projects/my-proj/roles/labCustom"]
  platforms: [gcp]
```

#### 2.2.5 GitHub — identity, repos, Actions, npm

```yaml
- name: github_pat
  description: "GitHub personal access token (classic or fine-grained)."
  schema:
    type: string
    pattern: "^(ghp_|github_pat_)[A-Za-z0-9_]+$"
  sensitive: true
  examples: ["ghp_test_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"]
  notes_for_generator: |
    Classic PATs start with `ghp_`. Fine-grained PATs start with `github_pat_`.
    The lab_credentials registry has a canonical fake (`ghp_test_*`) for planting
    in seeded content (e.g., committed repo content with a leaked PAT).
    Cleanup: PATs are revoked via API DELETE /authorizations or user revokes manually.
  platforms: [github]

- name: github_app_token
  description: "GitHub App installation token."
  schema:
    type: string
    pattern: "^ghs_[A-Za-z0-9_]+$"
  sensitive: true
  notes_for_generator: |
    Short-lived (1 hour). Obtained via JWT exchange. Used by GitHub Apps and
    by GitHub Actions (the `${{ secrets.GITHUB_TOKEN }}` form).
  platforms: [github]

- name: github_oauth_token
  description: "GitHub OAuth access token."
  schema:
    type: string
    pattern: "^gho_[A-Za-z0-9_]+$"
  sensitive: true
  platforms: [github]

- name: github_user_reference
  description: "GitHub user login plus optional id."
  schema:
    type: object
    required: [login]
    properties:
      login: { type: string }
      id: { type: integer, nullable: true }
  sensitive: false
  platforms: [github]

- name: github_organization_reference
  description: "GitHub organization login plus optional id."
  schema:
    type: object
    required: [login]
    properties:
      login: { type: string }
      id: { type: integer, nullable: true }
  sensitive: false
  platforms: [github]

- name: github_repo_reference
  description: "GitHub repository owner/name plus optional id."
  schema:
    type: object
    required: [owner, name]
    properties:
      owner: { type: string }
      name: { type: string }
      id: { type: integer, nullable: true }
  sensitive: false
  examples: [{ owner: "spotbugs", name: "sonar-findbugs" }]
  platforms: [github]

- name: github_branch_name
  description: "Git branch name plus repo reference."
  schema:
    type: object
    required: [owner, repo, branch]
    properties:
      owner: { type: string }
      repo: { type: string }
      branch: { type: string }
  sensitive: false
  notes_for_generator: |
    Branches with random suffixes (common in attack chains) are runtime_generated.
    Cleanup: `gh api -X DELETE repos/{owner}/{repo}/git/refs/heads/{branch}` or
    `git push origin --delete <branch>`. May return 404 if already deleted —
    not an error for cleanup purposes.
  cleanup_metadata: |
    Cleanup: delete via API or git push. 404 on cleanup is benign — the
    attack may have deleted the branch already (cover-tracks pattern).
  platforms: [github]

- name: github_commit_sha
  description: "Git commit SHA plus repo reference."
  schema:
    type: object
    required: [owner, repo, sha]
    properties:
      owner: { type: string }
      repo: { type: string }
      sha: { type: string, pattern: "^[0-9a-f]{7,40}$" }
  sensitive: false
  notes_for_generator: |
    Used heavily in "shadow commit" / fork-pivot attack chains (deleted forks
    leave SHAs traceable through the Events API even after deletion). The repo
    reference is needed because SHAs are repo-scoped, not global.
  platforms: [github]

- name: github_workflow_reference
  description: "GitHub Actions workflow file reference."
  schema:
    type: object
    required: [owner, repo, workflow_path]
    properties:
      owner: { type: string }
      repo: { type: string }
      workflow_path: { type: string, pattern: "^.github/workflows/.+\\.ya?ml$" }
  sensitive: false
  platforms: [github]

- name: github_workflow_run_id
  description: "GitHub Actions workflow run identifier."
  schema:
    type: object
    required: [owner, repo, run_id]
    properties:
      owner: { type: string }
      repo: { type: string }
      run_id: { type: integer }
  sensitive: false
  platforms: [github]

- name: github_pull_request_reference
  description: "Pull request number plus repo reference."
  schema:
    type: object
    required: [owner, repo, number]
    properties:
      owner: { type: string }
      repo: { type: string }
      number: { type: integer }
  sensitive: false
  notes_for_generator: |
    The `pull_request_target` trigger is the classic attack vector in supply-chain
    blogs. Lab should provision the workflow with the vulnerable trigger as
    attack_target.
  platforms: [github]

- name: github_actions_secret_reference
  description: "Reference to a GitHub Actions repository or organization secret."
  schema:
    type: object
    required: [scope, name]
    properties:
      scope: { type: string, enum: [repository, environment, organization] }
      name: { type: string }
      target: { type: string }     # repo name, env name, or org name depending on scope
  sensitive: false
  notes_for_generator: |
    The secret's *value* is sensitive but the reference (name) is not.
    Lab provisioning plants secrets; the attack reads them via job env exfiltration.
  platforms: [github]

- name: github_actions_runner_token
  description: "Self-hosted runner registration token."
  schema:
    type: string
    minLength: 16
  sensitive: true
  platforms: [github]

- name: github_release_reference
  description: "Repository release tag and optional asset URL."
  schema:
    type: object
    required: [owner, repo, tag_name]
    properties:
      owner: { type: string }
      repo: { type: string }
      tag_name: { type: string }
      asset_url: { type: string, format: uri, nullable: true }
  sensitive: false
  platforms: [github]
```

#### 2.2.6 npm and other package ecosystems

```yaml
- name: npm_token
  description: "npm authentication token (publish or read scope)."
  schema:
    type: string
    pattern: "^npm_[A-Za-z0-9]+$"
  sensitive: true
  notes_for_generator: |
    The classic Shai-Hulud-style supply-chain blog asset. Token granularity
    (publish vs read-only) matters; the lab should match the blog's scope claim.
  platforms: [npm]

- name: npm_package_reference
  description: "npm package name (optionally scoped) and version."
  schema:
    type: object
    required: [name]
    properties:
      name: { type: string }                 # may include "@scope/" prefix
      version: { type: string, nullable: true }
      registry_url: { type: string, format: uri, nullable: true }
  sensitive: false
  examples: [{ name: "@spotbugs/sonar-findbugs", version: "1.2.3" }]
  platforms: [npm]

- name: npm_registry_reference
  description: "npm registry URL (public, private, or simulated)."
  schema:
    type: string
    format: uri
  sensitive: false
  examples: ["https://registry.npmjs.org/", "http://localhost:4873/"]
  notes_for_generator: |
    Local-simulation labs use Verdaccio on port 4873 by default; the lab
    setup script provisions the Verdaccio container. The registry URL flows
    into the `.npmrc` of the victim build context.
  platforms: [npm]

- name: pypi_package_reference
  description: "PyPI package name and version."
  schema:
    type: object
    required: [name]
    properties:
      name: { type: string }
      version: { type: string, nullable: true }
  sensitive: false
  platforms: [pypi]

- name: pypi_token
  description: "PyPI API token (used for upload)."
  schema:
    type: string
    pattern: "^pypi-"
  sensitive: true
  platforms: [pypi]
```

#### 2.2.7 Generic and cross-platform types

```yaml
- name: sensitive_string
  description: "Catch-all sensitive string. Use only when no more-specific type fits."
  schema: { type: string }
  sensitive: true
  notes_for_generator: |
    Last-resort type. Prefer a specific type if the value has shape; the
    sensitive_string is a hint for the Generator to mark Terraform outputs
    `sensitive = true` and the runtime not to log the value.

- name: secret_value
  description: "Opaque secret value flowing between phases."
  schema: { type: string }
  sensitive: true

- name: ssh_private_key
  description: "SSH private key (OpenSSH format)."
  schema:
    type: string
    pattern: "^-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"
  sensitive: true

- name: ssh_public_key
  description: "SSH public key (single-line format)."
  schema:
    type: string
    pattern: "^(ssh-rsa|ssh-ed25519|ecdsa-sha2-)"
  sensitive: false

- name: vm_public_ip
  description: "Public IP address of a lab VM."
  schema:
    type: string
    oneOf:
      - { format: ipv4 }
      - { format: ipv6 }
  sensitive: false

- name: jwt_token
  description: "Generic JWT (header.payload.signature)."
  schema:
    type: string
    pattern: "^eyJ[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]+\\.[A-Za-z0-9_\\-]*$"
  sensitive: true

- name: oauth_authorization_code
  description: "OAuth 2.0 authorization code (short-lived, single-use)."
  schema:
    type: string
  sensitive: true

- name: bearer_token
  description: "Generic bearer token for HTTP Authorization headers."
  schema:
    type: string
  sensitive: true

- name: disk_seeded_file
  description: "Reference to a file the lab seeded on disk for the attack to discover."
  schema:
    type: object
    required: [path]
    properties:
      path: { type: string }
      contents_summary: { type: string, nullable: true }
      sensitivity_class: { type: string, enum: [credential, configuration, payload, decoy] }
  sensitive: false
  notes_for_generator: |
    Captures files the lab plants (e.g., leaked .aws/credentials in a build
    image). The `sensitivity_class` tells the cleanup which removal urgency applies.

- name: discovery_inventory
  description: "Generic inventory output from a reconnaissance step."
  schema:
    type: object
    required: [resource_type, items]
    properties:
      resource_type: { type: string }
      items: { type: array }
  sensitive: false
  notes_for_generator: |
    Output type for reconnaissance phases. Downstream phases iterate `items`
    and pick one to attack. resource_type is informational; the items shape
    depends on what was discovered.
```

#### 2.2.8 Kubernetes (cross-cloud)

Kubernetes is in scope across AWS (EKS), Azure (AKS), and GCP (GKE), so its types live in their own block:

```yaml
- name: k8s_sa_token
  description: "Kubernetes service account JWT token."
  schema:
    type: string
    pattern: "^eyJ"
  sensitive: true
  notes_for_generator: |
    The classic "/var/run/secrets/kubernetes.io/serviceaccount/token" exfiltration
    target. The kid header is significant for token validation and should be preserved.
  platforms: [aws, azure, gcp]

- name: k8s_pod_reference
  description: "Pod name, namespace, cluster context."
  schema:
    type: object
    required: [name, namespace]
    properties:
      name: { type: string }
      namespace: { type: string }
      cluster_context: { type: string, nullable: true }
  sensitive: false
  platforms: [aws, azure, gcp]

- name: k8s_secret_reference
  description: "Kubernetes Secret name plus namespace."
  schema:
    type: object
    required: [name, namespace]
    properties:
      name: { type: string }
      namespace: { type: string }
  sensitive: false
  platforms: [aws, azure, gcp]

- name: k8s_kubeconfig
  description: "Kubernetes kubeconfig YAML for cluster access."
  schema:
    type: string
  sensitive: true
  platforms: [aws, azure, gcp]
```

### 2.3 Coverage and growth

This v1 seed is ~75 entries spanning the platforms in scope. It is deliberately not exhaustive — the architecture's design point is that the Extractor proposes new entries when curated-set blogs surface them (`schema.md §4.16`). Coverage holes to expect overlay-fill in early use:

- Vendor-specific tokens beyond the Big Four (Cloudflare API tokens, Vercel deploy tokens, etc.).
- Specific token formats per cloud service (Azure SAS tokens, AWS pre-signed URLs, GCS signed URLs).
- Identity-federation specifics (SAML assertions, OIDC ID tokens with specific claim shapes).
- Container/registry image references with digest pinning.

The per-run cap on proposal acceptance (`schema.md §4.16`, default 5) keeps overlay growth honest in any single run; over many runs the overlay accumulates real coverage.

---

## 3. `facets` registry

**Path:** `registry/facets.yaml`
**Category:** Open-set, runtime-extensible. Split proposal authority by sub-category.
**Architectural reference:** `schema.md §4.13`.

### 3.1 Entry shape

Per `schema.md §4.13`. The Pydantic model is `FacetRegistryEntry` from `schema-details.md`.

```yaml
- name: target:aws                            # category:value form
  category: target                            # target | runtime | lab_class_signal
  proposed_by: extractor                      # extractor | planner — per category, not free choice
  description: "Attack targets AWS in some form (IAM, services, accounts)."
  applies_at_levels: [lab, phase]             # which levels can declare this facet
  requires_fields: []                         # additional schema fields required when declared
  implies: []                                 # facets automatically true when this is declared
  incompatible_with: []                       # facets that contradict this one
  examples: ["aws-iam-privesc", "aws-imds-exfil"]
  first_class: true                           # for runtime:* facets only; absent or false otherwise
  notes_for_extractor: |
    Use when the blog's attack chain involves AWS services or AWS identity material.
    Distinct from runtime:aws which is lab-declared (the lab provisions AWS resources).
```

The `category` field discriminates which agent proposes the facet:
- `target` → Extractor proposes (blog-derived).
- `runtime` → Planner proposes (lab-derived).
- `lab_class_signal` → split: blog-narrative signals by Extractor, lab-implementation signals by Planner; the `proposed_by` field on the entry records which.

### 3.2 v1 seed entries — `target:*` (extractor-proposed)

**Cloud platforms** (high-level):

```yaml
- name: target:aws
  category: target
  proposed_by: extractor
  description: "Attack targets AWS (services, IAM, accounts, infrastructure)."
  applies_at_levels: [lab, phase]
  implies: []
  notes_for_extractor: "Use when blog's chain involves AWS services or identity material."

- name: target:azure
  category: target
  proposed_by: extractor
  description: "Attack targets Azure (services, subscriptions, infrastructure)."
  applies_at_levels: [lab, phase]
  implies: []

- name: target:gcp
  category: target
  proposed_by: extractor
  description: "Attack targets Google Cloud Platform."
  applies_at_levels: [lab, phase]
  implies: []

- name: target:github
  category: target
  proposed_by: extractor
  description: "Attack targets GitHub (the platform — repos, orgs, Actions, etc.)."
  applies_at_levels: [lab, phase]
  implies: []
```

**Identity tiers** (more specific than the cloud they live in):

```yaml
- name: target:aws_iam
  category: target
  proposed_by: extractor
  description: "Attack targets AWS IAM specifically (users, roles, policies, STS)."
  applies_at_levels: [lab, phase]
  implies: [target:aws]

- name: target:entra_id
  category: target
  proposed_by: extractor
  description: "Attack targets Microsoft Entra ID (formerly Azure AD), the identity tier."
  applies_at_levels: [lab, phase]
  implies: [target:azure]
  examples: ["dirk-jan-entra-actor-tokens"]

- name: target:gcp_iam
  category: target
  proposed_by: extractor
  description: "Attack targets GCP IAM (service accounts, workload identity, policies)."
  applies_at_levels: [lab, phase]
  implies: [target:gcp]
```

**Compute / serverless surfaces:**

```yaml
- name: target:aws_lambda
  category: target
  proposed_by: extractor
  description: "Attack targets AWS Lambda (functions, execution roles, layers)."
  applies_at_levels: [lab, phase]
  implies: [target:aws]

- name: target:aws_ec2
  category: target
  proposed_by: extractor
  description: "Attack targets EC2 instances (IMDS, instance roles, security groups)."
  applies_at_levels: [lab, phase]
  implies: [target:aws]

- name: target:aws_s3
  category: target
  proposed_by: extractor
  description: "Attack targets S3 (buckets, objects, bucket policies, ACLs)."
  applies_at_levels: [lab, phase]
  implies: [target:aws]

- name: target:azure_functions
  category: target
  proposed_by: extractor
  description: "Attack targets Azure Functions."
  applies_at_levels: [lab, phase]
  implies: [target:azure]

- name: target:gcp_cloud_run
  category: target
  proposed_by: extractor
  description: "Attack targets GCP Cloud Run."
  applies_at_levels: [lab, phase]
  implies: [target:gcp]
```

**Kubernetes (per-cloud):**

```yaml
- name: target:kubernetes
  category: target
  proposed_by: extractor
  description: "Attack targets Kubernetes (cluster-level, regardless of provider)."
  applies_at_levels: [lab, phase]

- name: target:eks
  category: target
  proposed_by: extractor
  description: "Attack targets Amazon EKS."
  applies_at_levels: [lab, phase]
  implies: [target:aws, target:kubernetes]

- name: target:aks
  category: target
  proposed_by: extractor
  description: "Attack targets Azure AKS."
  applies_at_levels: [lab, phase]
  implies: [target:azure, target:kubernetes]

- name: target:gke
  category: target
  proposed_by: extractor
  description: "Attack targets Google GKE."
  applies_at_levels: [lab, phase]
  implies: [target:gcp, target:kubernetes]
```

**GitHub sub-surfaces:**

```yaml
- name: target:github_actions
  category: target
  proposed_by: extractor
  description: "Attack targets GitHub Actions workflows / runners / secrets."
  applies_at_levels: [lab, phase]
  implies: [target:github]

- name: target:github_apps
  category: target
  proposed_by: extractor
  description: "Attack targets GitHub Apps (installations, permissions, JWT exchange)."
  applies_at_levels: [lab, phase]
  implies: [target:github]
```

**Package ecosystems:**

```yaml
- name: target:npm_registry
  category: target
  proposed_by: extractor
  description: "Attack targets npm registry (supply-chain via published packages)."
  applies_at_levels: [lab, phase]

- name: target:pypi
  category: target
  proposed_by: extractor
  description: "Attack targets PyPI (supply-chain via published packages)."
  applies_at_levels: [lab, phase]

- name: target:container_registry
  category: target
  proposed_by: extractor
  description: "Attack targets container registry (Docker Hub, ECR, GAR, ACR)."
  applies_at_levels: [lab, phase]
```

### 3.3 v1 seed entries — `runtime:*` (planner-proposed)

```yaml
- name: runtime:aws
  category: runtime
  proposed_by: planner
  description: "Lab provisions resources in real AWS and runs the attack against them."
  applies_at_levels: [lab]
  first_class: true
  requires_fields: []
  implies: []
  notes_for_planner: |
    Lab-derived. The Planner declares this when generating provisioning that targets AWS.
    Declares first-class Layer 4 verification coverage when v2 ships.

- name: runtime:azure
  category: runtime
  proposed_by: planner
  description: "Lab provisions resources in real Azure and runs the attack against them."
  applies_at_levels: [lab]
  first_class: true

- name: runtime:gcp
  category: runtime
  proposed_by: planner
  description: "Lab provisions resources in real GCP and runs the attack against them."
  applies_at_levels: [lab]
  first_class: true

- name: runtime:github
  category: runtime
  proposed_by: planner
  description: "Lab provisions GitHub resources (repos, orgs, Actions workflows) and runs against them."
  applies_at_levels: [lab]
  first_class: true

- name: runtime:local
  category: runtime
  proposed_by: planner
  description: "Lab runs entirely on the user's local machine (containers, simulated services)."
  applies_at_levels: [lab]
  first_class: false
  notes_for_planner: |
    Used for fully-simulated labs. Common companion of partial_simulation labs that
    swap real-platform components for local services (Verdaccio for npm, MinIO for S3).
```

Best-effort runtimes (`first_class: false`) are proposed at runtime by the Planner; examples include `runtime:cloudflare`, `runtime:vercel`, `runtime:digitalocean`. None ship as seeds.

### 3.4 v1 seed entries — `lab_class_signal:*` (split authorship)

**Blog-derived (extractor proposes):**

```yaml
- name: lab_class_signal:incident_analysis
  category: lab_class_signal
  proposed_by: extractor
  description: "Blog is an incident analysis (post-event), not a vulnerability disclosure or TTP walk-through."
  applies_at_levels: [lab]
  notes_for_extractor: |
    Declare when the blog describes a real incident with affected parties named.
    Implies the AttackSpec will populate `defender_techniques` and `real_world_incidents`.

- name: lab_class_signal:vulnerability_chain
  category: lab_class_signal
  proposed_by: extractor
  description: "Blog focuses on a chain of vulnerabilities composed to escalate access."
  applies_at_levels: [lab]

- name: lab_class_signal:misconfiguration
  category: lab_class_signal
  proposed_by: extractor
  description: "Blog focuses on misconfigurations (rather than CVE-style flaws) as the primary surface."
  applies_at_levels: [lab]

- name: lab_class_signal:supply_chain_compromise
  category: lab_class_signal
  proposed_by: extractor
  description: "Blog describes a supply-chain compromise (compromised package, CI, build dep)."
  applies_at_levels: [lab]

- name: lab_class_signal:cross_tenant_compromise
  category: lab_class_signal
  proposed_by: extractor
  description: "Blog describes attacker crossing tenant/account boundaries."
  applies_at_levels: [lab]

- name: lab_class_signal:cloud_provider_flaw
  category: lab_class_signal
  proposed_by: extractor
  description: "Blog describes a flaw in the cloud provider's own systems (not the customer's config)."
  applies_at_levels: [lab]
  notes_for_extractor: |
    Typically vendor-only defenses. Lab generation reproduces the customer-visible
    surface but the actual flaw fix is "the vendor patched it."

- name: lab_class_signal:external_channel
  category: lab_class_signal
  proposed_by: extractor
  description: "Attack relies on an external channel (DNS exfil, webhook, external C2)."
  applies_at_levels: [lab, phase]

- name: lab_class_signal:time_marked
  category: lab_class_signal
  proposed_by: extractor
  description: "Attack chain or detection depends on time-of-day, day-of-week, or timing windows."
  applies_at_levels: [lab, phase]
  notes_for_extractor: |
    Examples: attacks that only fire during business hours; detections that use
    time-of-day correlation. The lab must handle the time dimension somehow.

- name: lab_class_signal:expected_detections
  category: lab_class_signal
  proposed_by: extractor
  description: "Blog explicitly lists detections (Sigma, KQL, etc.) that should fire on the attack."
  applies_at_levels: [lab]

- name: lab_class_signal:detection_methodology
  category: lab_class_signal
  proposed_by: extractor
  description: "Blog is primarily about *how* to detect a class of attacks (methodology, not specific TTP)."
  applies_at_levels: [lab]
```

**Lab-derived (planner proposes):**

```yaml
- name: lab_class_signal:simulated_components
  category: lab_class_signal
  proposed_by: planner
  description: "Lab uses simulated/substituted components (Verdaccio, MinIO, local Gitea) for some real platform pieces."
  applies_at_levels: [lab, phase]
  notes_for_planner: |
    Planner-declared once the lab plan substitutes a local-simulation for a real service.
    Drives docs ("note: this phase uses Verdaccio instead of real npm").

- name: lab_class_signal:multi_language
  category: lab_class_signal
  proposed_by: planner
  description: "Lab uses multiple programming languages across phases."
  applies_at_levels: [lab]
  notes_for_planner: "Typically when a payload language differs from the orchestration language."

- name: lab_class_signal:parameterized
  category: lab_class_signal
  proposed_by: planner
  description: "Lab exposes meaningful run-time parameters that change behavior (not just credentials)."
  applies_at_levels: [lab]

- name: lab_class_signal:requires_infra
  category: lab_class_signal
  proposed_by: planner
  description: "Lab requires non-trivial infrastructure beyond a single function or VM."
  applies_at_levels: [lab]

- name: lab_class_signal:produces_world_state
  category: lab_class_signal
  proposed_by: planner
  description: "Phases produce world state beyond their IaC (must be tracked for cleanup)."
  applies_at_levels: [lab, phase]
  notes_for_planner: |
    Triggers stricter cleanup-confidence gating per architecture.md §1.6 (the
    cleanup must demonstrate coverage of all declared produces_world_state items).

- name: lab_class_signal:manual_prereq
  category: lab_class_signal
  proposed_by: planner
  description: "Lab has manual user prerequisites (pre_lab or mid_lab) the orchestrator cannot automate."
  applies_at_levels: [lab]
  notes_for_planner: "Triggers the prereqs block; the Docs Generator surfaces a 'Prerequisites' section."

- name: lab_class_signal:waits_for_condition
  category: lab_class_signal
  proposed_by: planner
  description: "Lab requires waiting for an external condition (scanner run, registry propagation, TTL expiry)."
  applies_at_levels: [lab, phase]
  notes_for_planner: "Surfaced as a mid_lab prereq with a wait-and-confirm prompt."
```

### 3.5 v1.5+ deferrals — defender and observer perspectives

Defender-mode and observer-mode labs are deferred to v1.5+ (`architecture.md §8.2`). The v1 schema deliberately does **not** reserve facet namespace for them; the `FacetName` pattern in `schema-details.md §2.1` does not accept any prefix beyond `target:*`, `runtime:*`, and `lab_class_signal:*`.

Reintroducing the relevant facet category alongside its consuming code (defender-side and observer-side generator templates, agent prompts, and validation paths) in v1.5+ is a clean schema-version bump under the no-migration discipline (`architecture.md §0.6`). v1 has no code that consumes actor-perspective declarations, so the schema doesn't accommodate them — neither in the regex, nor in the FacetEntry category Literal, nor in this registry's seed.

---

## 4. `external_data_sources` registry

**Path:** `registry/external_data_sources.yaml`
**Category:** Closed-set, maintainer-only. No runtime proposals (`schema.md §4.16`: adding a new source typically requires code changes for auth handling and response parsing).
**Architectural reference:** `schema.md §4.14`.

### 4.1 Entry shape

Per `schema.md §4.14`. Every field below is non-optional unless marked; the meta-schema is enforced at Layer 1.

```yaml
- id: nvd
  name: "National Vulnerability Database"
  description: "Authoritative CVE metadata cross-cloud."
  base_url: "https://services.nvd.nist.gov/rest/json/cves/2.0"
  auth_type: optional_api_key
  auth_env_var: NVD_API_KEY
  rate_limit:
    without_key: "5 requests per 30 seconds"
    with_key: "50 requests per 30 seconds"
  endpoints:
    - id: lookup_cve
      method: GET
      path_template: "?cveId={cve_id}"
      parameters:                             # dict keyed by param name (per schema-details.md §6.3)
        cve_id:
          type: string
          required: true
          pattern: "^CVE-[0-9]{4}-[0-9]+$"
      response_schema_ref: nvd_cve_response_v2   # resolved by adapter in cyberlab_gen/external_data_sources/
      cache_ttl: P7D                          # ISO 8601 duration; Pydantic parses to timedelta
  enrichment_triggers:
    - field: "chain.chain_steps[*].techniques.mitre[*].cve_ids[*]"
      action: lookup
      endpoint: lookup_cve
    - field: "external_references.cve_references[*]"
      action: lookup
      endpoint: lookup_cve
  discrepancy_materiality_rules:
    - field_path: cvss_score
      classification: material
      rule_description: "CVSS contradiction between blog and NVD changes severity narrative."
    - field_path: affected_products
      classification: material
      rule_description: "Wrong affected products misleads the user about lab applicability."
    - field_path: description
      classification: non_material
      rule_description: "Wording differences are expected; semantic equivalence is what matters."
  cache:
    ttl: P7D                                  # ISO 8601 duration
    scope: per-key
  best_effort: false
  notes_for_extractor: |
    Authoritative for CVE metadata (CVSS, affected products, references).
    For Microsoft-issued CVEs, prefer MSRC for the affected-products detail and use
    NVD for CVSS. Distinct from KEV (which captures known exploitation, not metadata).
```

### 4.2 v1 entries

The full set per `schema.md §4.14`. Each entry is specified concretely below.

```yaml
- id: nvd
  name: "National Vulnerability Database"
  description: "Authoritative CVE metadata cross-cloud (CVSS, affected products, references)."
  base_url: "https://services.nvd.nist.gov/rest/json/cves/2.0"
  auth_type: optional_api_key
  auth_env_var: NVD_API_KEY
  rate_limit:
    without_key: "5 requests per 30 seconds"
    with_key: "50 requests per 30 seconds"
  endpoints:
    - id: lookup_cve
      method: GET
      path_template: "?cveId={cve_id}"
      parameters:
        cve_id: { type: string, required: true, pattern: "^CVE-[0-9]{4}-[0-9]+$" }
      response_schema_ref: nvd_cve_response_v2
      cache_ttl: P7D
  enrichment_triggers:
    - { field: "chain.chain_steps[*].techniques.mitre[*].cve_ids[*]", action: lookup, endpoint: lookup_cve }
    - { field: "external_references.cve_references[*]", action: lookup, endpoint: lookup_cve }
  discrepancy_materiality_rules:
    - { field_path: cvss_score, classification: material, rule_description: "CVSS contradiction changes severity narrative." }
    - { field_path: affected_products, classification: material, rule_description: "Wrong affected products misleads about lab applicability." }
    - { field_path: description, classification: non_material, rule_description: "Wording differences expected." }
  cache: { ttl: P7D, scope: per-key }
  best_effort: false
  notes_for_extractor: |
    Authoritative for CVE metadata. For Microsoft-issued CVEs, cross-check with MSRC.

- id: msrc
  name: "Microsoft Security Response Center"
  description: "Authoritative for Microsoft-issued CVEs (Azure, Entra, Microsoft 365)."
  base_url: "https://api.msrc.microsoft.com/cvrf/v2.0"
  auth_type: none
  rate_limit:
    without_key: "unspecified; rate-limit at HTTP layer"
  endpoints:
    - id: lookup_cve
      method: GET
      path_template: "/cvrf/{cve_id}"
      parameters:
        cve_id: { type: string, required: true, pattern: "^CVE-[0-9]{4}-[0-9]+$" }
      response_schema_ref: msrc_cvrf_response_v2
      cache_ttl: P7D
  enrichment_triggers:
    - { field: "external_references.cve_references[?prefix='CVE'][?starts_with('Microsoft')]", action: lookup, endpoint: lookup_cve }
  discrepancy_materiality_rules:
    - { field_path: affected_products, classification: material, rule_description: "MSRC is authoritative for Microsoft products." }
    - { field_path: fix_version, classification: material, rule_description: "Wrong fix version misleads remediation guidance." }
  cache: { ttl: P7D, scope: per-key }
  best_effort: false
  notes_for_extractor: "Use in addition to NVD when the CVE is Microsoft-issued."

- id: mitre_attack
  name: "MITRE ATT&CK"
  description: "STIX/TAXII technique data (T-codes, tactics, descriptions)."
  base_url: "https://attack.mitre.org/api"
  auth_type: none
  rate_limit:
    without_key: "static data; rate-limit minimal"
  endpoints:
    - id: lookup_technique
      method: GET
      path_template: "/techniques/{technique_id}"
      parameters:
        technique_id: { type: string, required: true, pattern: "^T[0-9]{4}(\\.[0-9]{3})?$" }
      response_schema_ref: mitre_technique_response
      cache_ttl: P30D
  enrichment_triggers:
    - { field: "chain.chain_steps[*].techniques.mitre[*]", action: lookup, endpoint: lookup_technique }
  discrepancy_materiality_rules:
    - { field_path: technique_id, classification: material, rule_description: "Hallucinated technique IDs fail validation." }
    - { field_path: tactic, classification: material, rule_description: "Wrong tactic mapping breaks the kill-chain narrative." }
  cache: { ttl: P30D, scope: global }
  best_effort: false
  notes_for_extractor: |
    The bundled MITRE reference (`registry/mitre-attack/`) is used at validation time;
    this entry is for runtime lookup of technique details when MITRE refresh ships.

- id: osv_dev
  name: "OSV.dev"
  description: "Cross-ecosystem vulnerability advisories (npm, PyPI, Cargo, Go, etc.)."
  base_url: "https://api.osv.dev/v1"
  auth_type: none
  rate_limit:
    without_key: "unspecified; rate-limit at HTTP layer"
  endpoints:
    - id: lookup_package
      method: POST
      path_template: "/query"
      parameters:
        package_name: { type: string, required: true }
        ecosystem: { type: string, required: true, enum_values: [npm, PyPI, crates.io, Go, Maven, RubyGems] }
        version: { type: string, required: false }
      response_schema_ref: osv_query_response
      cache_ttl: P3D
  enrichment_triggers:
    - { field: "chain.chain_steps[*].targets.packages[*]", action: lookup, endpoint: lookup_package }
  discrepancy_materiality_rules:
    - { field_path: affected_versions, classification: material, rule_description: "Wrong affected versions changes lab applicability." }
    - { field_path: severity, classification: material, rule_description: "Cross-ecosystem severity reframes the attack." }
  cache: { ttl: P3D, scope: per-key }
  best_effort: false
  notes_for_extractor: "Authoritative for package-ecosystem advisories beyond what NVD covers."

- id: github_api
  name: "GitHub REST API"
  description: "Repository metadata, file content, issue/PR data for blog-cited repos."
  base_url: "https://api.github.com"
  auth_type: optional_api_key
  auth_env_var: GITHUB_TOKEN
  rate_limit:
    without_key: "60 requests per hour"
    with_key: "5000 requests per hour"
  endpoints:
    - id: lookup_repo
      method: GET
      path_template: "/repos/{owner}/{repo}"
      parameters:
        owner: { type: string, required: true }
        repo: { type: string, required: true }
      response_schema_ref: github_repo_response
      cache_ttl: P1D
    - id: get_file_contents
      method: GET
      path_template: "/repos/{owner}/{repo}/contents/{path}"
      parameters:
        owner: { type: string, required: true }
        repo: { type: string, required: true }
        path: { type: string, required: true }
        ref: { type: string, required: false }
      response_schema_ref: github_contents_response
      cache_ttl: P1D
    - id: list_workflow_runs
      method: GET
      path_template: "/repos/{owner}/{repo}/actions/runs"
      parameters:
        owner: { type: string, required: true }
        repo: { type: string, required: true }
      response_schema_ref: github_workflow_runs_response
      cache_ttl: PT1H
  enrichment_triggers:
    - { field: "chain.chain_steps[*].targets.repos[*]", action: lookup, endpoint: lookup_repo }
  discrepancy_materiality_rules:
    - { field_path: default_branch, classification: non_material, rule_description: "Branch rename doesn't change attack semantics." }
    - { field_path: archived, classification: material, rule_description: "Archived repo affects whether the attack is still reproducible." }
    - { field_path: visibility, classification: material, rule_description: "Public-vs-private changes the attack premise." }
  cache: { ttl: P1D, scope: per-key }
  best_effort: false
  notes_for_extractor: |
    Use for grounding claims about specific repos (existence, visibility, default branch).
    Don't use for content that's blog-narrative — read those from the blog.

- id: aws_security_bulletins
  name: "AWS Security Bulletins"
  description: "AWS-side security disclosures via RSS."
  base_url: "https://aws.amazon.com/security/security-bulletins/rss/feed/"
  auth_type: none
  rate_limit:
    without_key: "RSS feed; polling-friendly"
  endpoints:
    - id: list_recent
      method: GET
      path_template: ""
      parameters: {}
      response_schema_ref: rss_feed
      cache_ttl: P1D
  enrichment_triggers:
    - { field: "facets[?value='target:aws']", action: lookup, endpoint: list_recent }
  discrepancy_materiality_rules:
    - { field_path: bulletin_status, classification: material, rule_description: "Active-vs-mitigated bulletin status changes lab framing." }
  cache: { ttl: P1D, scope: global }
  best_effort: true
  notes_for_extractor: "RSS feeds can break format unexpectedly; best_effort tolerates unavailability."

- id: azure_security_advisories
  name: "Azure Security Advisories (MSRC feed)"
  description: "Azure-side security disclosures via MSRC feed."
  base_url: "https://msrc.microsoft.com/update-guide/rss"
  auth_type: none
  rate_limit:
    without_key: "RSS feed"
  endpoints:
    - id: list_recent
      method: GET
      path_template: ""
      parameters: {}
      response_schema_ref: rss_feed
      cache_ttl: P1D
  enrichment_triggers:
    - { field: "facets[?value='target:azure']", action: lookup, endpoint: list_recent }
  cache: { ttl: P1D, scope: global }
  best_effort: false

- id: gcp_security_bulletins
  name: "GCP Security Bulletins"
  description: "GCP-side security disclosures via RSS / cloud.google.com."
  base_url: "https://cloud.google.com/feeds/security-bulletins.xml"
  auth_type: none
  rate_limit:
    without_key: "RSS feed"
  endpoints:
    - id: list_recent
      method: GET
      path_template: ""
      parameters: {}
      response_schema_ref: rss_feed
      cache_ttl: P1D
  enrichment_triggers:
    - { field: "facets[?value='target:gcp']", action: lookup, endpoint: list_recent }
  cache: { ttl: P1D, scope: global }
  best_effort: true

- id: cisa_kev
  name: "CISA Known Exploited Vulnerabilities"
  description: "Vulnerabilities CISA tracks as actively exploited in the wild."
  base_url: "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
  auth_type: none
  rate_limit:
    without_key: "static JSON; polling-friendly"
  endpoints:
    - id: lookup_cve_in_kev
      method: GET
      path_template: ""
      parameters: {}
      response_schema_ref: kev_catalog
      cache_ttl: P1D
  enrichment_triggers:
    - { field: "external_references.cve_references[*]", action: lookup, endpoint: lookup_cve_in_kev }
  discrepancy_materiality_rules:
    - { field_path: kev_inclusion, classification: material, rule_description: "KEV inclusion is a substantive 'this is actively exploited' signal." }
  cache: { ttl: P1D, scope: global }
  best_effort: false
  notes_for_extractor: |
    The catalog is downloaded once and queried locally. Each CVE in the AttackSpec
    is checked; KEV inclusion is recorded in provenance.

- id: epss
  name: "Exploit Prediction Scoring System"
  description: "EPSS scores for CVEs (probability of exploitation in next 30 days)."
  base_url: "https://api.first.org/data/v1/epss"
  auth_type: none
  rate_limit:
    without_key: "unspecified; rate-limit at HTTP layer"
  endpoints:
    - id: lookup_epss
      method: GET
      path_template: "?cve={cve_id}"
      parameters:
        cve_id: { type: string, required: true, pattern: "^CVE-[0-9]{4}-[0-9]+$" }
      response_schema_ref: epss_response
      cache_ttl: P1D
  enrichment_triggers:
    - { field: "external_references.cve_references[*]", action: lookup, endpoint: lookup_epss }
  discrepancy_materiality_rules:
    - { field_path: epss_score, classification: non_material, rule_description: "EPSS is contextual signal, not a contradiction-trigger." }
  cache: { ttl: P1D, scope: per-key }
  best_effort: false
```

### 4.3 What is *not* in `external_data_sources`

Per `schema.md §4.14`, the following are explicitly deferred from v1:

- Vendor-specific advisory feeds beyond the Big Three clouds (Oracle, IBM, etc.).
- Language-ecosystem-specific sources beyond OSV.dev (PyPI advisories direct, RubySec direct, etc. — OSV aggregates these).
- Threat intel feeds.

Adding any of these is a maintainer PR plus code changes (new auth handling, response parsing, materiality rules).

---

## 5. `static_catalogs` registry

**Path:** `registry/static_catalogs.yaml`
**Category:** Closed-set, maintainer-only. No `enrichment_triggers` field (consulted on-demand by the Generator and Validator, not by automatic enrichment).
**Architectural reference:** `schema.md §4.14`.

### 5.1 Entry shape

Entries use the `StaticCatalogEntry` Pydantic shape (`schema-details.md §6.3`), which shares its common fields with `ExternalDataSourceEntry` via a private base but: omits `enrichment_triggers` and `discrepancy_materiality_rules` (consulted on-demand, not enrichment-triggered); replaces `notes_for_extractor` with `notes_for_generator` (the Generator is the consumer for static catalogs, not the Extractor). The two classes are mechanically distinct under `extra="forbid"` — a static_catalogs entry with `enrichment_triggers` fails Layer 1; an external_data_sources entry with `notes_for_generator` likewise.

### 5.2 v1 entries

```yaml
- id: aws_iam_catalog
  name: "AWS IAM Action Catalog"
  description: "Static JSON of all AWS IAM actions, resource type combinations, and condition keys."
  base_url: "https://awspolicygen.s3.amazonaws.com/js/policies.js"
  auth_type: none
  rate_limit:
    without_key: "static asset; polling-friendly"
  endpoints:
    - id: catalog_download
      method: GET
      path_template: ""
      parameters: {}
      response_schema_ref: aws_iam_actions_catalog
      cache_ttl: P30D
  cache: { ttl: P30D, scope: global }
  best_effort: false
  notes_for_generator: |
    Consulted via lookup_cloud_iam_action(cloud='aws', action='<service>:<Action>')
    to verify the action exists and the policy is syntactically valid. Generator
    must not emit IAM actions that fail this lookup; Validator catches them.

- id: azure_rbac_catalog
  name: "Azure RBAC Catalog"
  description: "Static JSON of Azure built-in roles, actions, and dataActions."
  base_url: "https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles"
  auth_type: none
  rate_limit:
    without_key: "static; polling-friendly"
  endpoints:
    - id: catalog_download
      method: GET
      path_template: ""
      parameters: {}
      response_schema_ref: azure_rbac_catalog
      cache_ttl: P30D
  cache: { ttl: P30D, scope: global }
  best_effort: false
  notes_for_generator: |
    Consulted via lookup_cloud_iam_action(cloud='azure', action='<provider>/<scope>/<verb>').
    Azure's action verbs are URN-shaped (Microsoft.Compute/virtualMachines/read), distinct
    from AWS's service:Action shape.

- id: gcp_iam_catalog
  name: "GCP IAM Permissions Catalog"
  description: "Static JSON of GCP IAM permissions and predefined roles."
  base_url: "https://cloud.google.com/iam/docs/permissions-reference"
  auth_type: none
  rate_limit:
    without_key: "static; polling-friendly"
  endpoints:
    - id: catalog_download
      method: GET
      path_template: ""
      parameters: {}
      response_schema_ref: gcp_iam_catalog
      cache_ttl: P30D
  cache: { ttl: P30D, scope: global }
  best_effort: false
  notes_for_generator: |
    Consulted via lookup_cloud_iam_action(cloud='gcp', action='<service>.<resource>.<verb>').
    GCP permissions are dot-shaped (compute.instances.get).
```

---

## 6. `execution_contexts` registry

**Path:** `registry/execution_contexts.yaml`
**Category:** Open-set, planner-extensible (per `schema.md §4.5`, §4.16). New entries are rare in practice — mostly maintainer-curated.
**Architectural reference:** `schema.md §4.5`, `agents.md §5.10`.

### 6.1 Entry shape

```yaml
- name: attacker_local
  description: "Code runs on the attacker's local machine (user's terminal during lab execution)."
  credential_assumption: "Attacker has the lab's configured cloud credentials available via standard tooling conventions."
  network_assumption: "Outbound network to target cloud APIs and the lab's IaC-provisioned public endpoints."
  notes_for_generator: |
    Most common execution context. The code uses local SDK clients (boto3, azure-sdk, etc.)
    with credentials sourced from environment or standard cred chains. No special bootstrap.
```

### 6.2 v1 entries

The seven contexts enumerated in `agents.md §5.10` plus `other` as the registry-evolution escape valve:

```yaml
- name: attacker_local
  description: "Code runs on the attacker's local machine."
  credential_assumption: "Lab's configured cloud credentials via standard tooling conventions."
  network_assumption: "Outbound to target cloud APIs and lab-provisioned endpoints."
  notes_for_generator: |
    Most common. Uses local SDK clients with credentials from environment.
    No special bootstrap; the lab's `setup.sh` validated credentials at startup.

- name: victim_vm_via_ssh
  description: "Code runs on a victim VM accessed by SSH from the attacker's machine."
  credential_assumption: "SSH key into the VM (provisioned by lab); the VM's instance role provides cloud creds inside."
  network_assumption: "Inside the VM's network context (typically a private subnet with NAT)."
  notes_for_generator: |
    Wrap the attack code in `ssh ... <<EOF ... EOF` or use a remote-exec helper.
    The code inside the VM uses the VM's instance role (IMDS-derived creds), not the
    attacker's local credentials.

- name: victim_lambda
  description: "Code runs inside a victim Lambda function execution."
  credential_assumption: "Lambda execution role credentials available via the runtime environment."
  network_assumption: "Lambda's network context (VPC if configured, otherwise AWS internal)."
  notes_for_generator: |
    The function body itself is the 'attack code' — it inherits the execution role
    privileges. Common scenarios: deserialization RCE giving function-equivalent
    access, or a malicious Lambda planted by an earlier phase.

- name: victim_build_container
  description: "Code runs inside a build pipeline container (Docker build, CI worker, etc.)."
  credential_assumption: "Build credentials (registry tokens, deploy keys, optional cloud creds for IaC builds)."
  network_assumption: "Build environment network — typically outbound to package registries; varies by CI provider."
  notes_for_generator: |
    Supply-chain context. The malicious code runs as part of npm install / docker build
    and exfils credentials available in the build env. Bootstrap via package preinstall
    hooks or Dockerfile RUN instructions.

- name: victim_serverless
  description: "Code runs in a generic serverless context (Azure Functions, Cloud Run, etc.)."
  credential_assumption: "Function/service identity (managed identity, workload identity, etc.)."
  network_assumption: "Serverless platform network."
  notes_for_generator: |
    Use when the specific platform is not Lambda. The Generator chooses the appropriate
    runtime invocation pattern based on which platform the manifest specifies.

- name: victim_pod
  description: "Code runs inside a Kubernetes pod."
  credential_assumption: "Service account token mounted at /var/run/secrets/kubernetes.io/serviceaccount/."
  network_assumption: "Pod's network namespace; cluster-internal addressing via DNS."
  notes_for_generator: |
    The service-account token is the typical credential vector. Cross-cluster
    attacks use this context with the IRSA / workload-identity pattern.

- name: github_actions_runner
  description: "Code runs inside a GitHub Actions runner during a workflow execution."
  credential_assumption: "GITHUB_TOKEN available via env var; secrets accessible via ${{ secrets.* }}."
  network_assumption: "Runner network (GitHub-hosted or self-hosted)."
  notes_for_generator: |
    The classic supply-chain attack context. Code lives in a workflow file or in
    a malicious action; runs with the workflow's effective token permissions.
    pull_request_target trigger is the classic over-privilege vector.

- name: other
  description: "Escape valve for execution contexts not yet enumerated."
  credential_assumption: "Specified per lab in the manifest's `execution_context_notes` field."
  network_assumption: "Specified per lab."
  notes_for_generator: |
    Use only when the lab's context genuinely doesn't fit any enumerated entry.
    When chosen, the Planner should also propose a new context entry via the
    standard proposal flow — `other` should rarely persist into a final manifest.
```

---

## 7. Closed bundled-only catalogs

These are not really "registries" in the proposal-flow sense; they're enumerated closed sets the architecture pins. Documented here so the v1 contents are unambiguous.

### 7.1 `detection_components`

**Path:** `registry/detection_components.yaml`
**Closed enum.** Per `schema.md §4.7`. New entries by maintainer PR only.

```yaml
- name: CSPM
  display_name: "Cloud Security Posture Management"
  description: "Misconfiguration and posture detection across cloud accounts."

- name: CWP
  display_name: "Cloud Workload Protection"
  description: "Workload-level detection (host, container, serverless)."

- name: CDR
  display_name: "Cloud Detection and Response"
  description: "Behavioral and runtime detection of cloud-native attacks."

- name: CIEM
  display_name: "Cloud Infrastructure Entitlement Management"
  description: "Identity, entitlement, and access-path detection."

- name: DSPM
  display_name: "Data Security Posture Management"
  description: "Data-classification-aware posture and access monitoring."

- name: ASPM
  display_name: "Application Security Posture Management"
  description: "Application-layer posture (code, dependencies, deployments)."

- name: ITDR
  display_name: "Identity Threat Detection and Response"
  description: "Identity-focused detection (token misuse, impersonation chains)."

- name: KSPM
  display_name: "Kubernetes Security Posture Management"
  description: "Kubernetes-specific misconfiguration and runtime posture."

- name: API_Security
  display_name: "API Security"
  description: "API-layer detection of abuse, abuse-of-functionality, and authn/authz drift."

- name: Supply_Chain_Security
  display_name: "Supply Chain Security"
  description: "Detection across the build and dependency chain."
```

### 7.2 `severity_levels`

**Path:** `registry/severity_levels.yaml` (or inlined in the schema; both acceptable since this is a 4-element enum).
**Closed enum.** Per `schema.md §4.7`.

```yaml
- name: Critical
  ordinal: 4
- name: High
  ordinal: 3
- name: Medium
  ordinal: 2
- name: Low
  ordinal: 1
```

The `ordinal` field exists for cross-severity comparison (e.g., the validator's "severity floor" rules in Layer 3).

### 7.3 `detection_formats`

**Path:** `registry/detection_formats.yaml`
**Closed enum.** Per `schema.md §4.7`.

```yaml
- name: sigma
  display_name: "Sigma"
  file_extension: ".yml"
  description: "Portable, SIEM-agnostic detection format."

- name: kql
  display_name: "Kusto Query Language (KQL)"
  file_extension: ".kql"
  description: "Microsoft Sentinel / Defender 365 native query language."

- name: spl
  display_name: "Search Processing Language (SPL)"
  file_extension: ".spl"
  description: "Splunk native search language."

- name: esql
  display_name: "Elasticsearch ESQL"
  file_extension: ".esql"
  description: "Elastic's piped query language."
```

### 7.4 `provisioning_mechanisms`

**Path:** `registry/provisioning_mechanisms.yaml`
**Closed enum.** Per `schema.md §4.5`. Adding new ones is non-trivial integration; deferred to v1.5+.

```yaml
- name: terraform
  display_name: "Terraform"
  description: "HashiCorp Terraform. Preferred where supported (cross-cloud, broad validator coverage)."
  validator_support: full     # tflint, terraform validate, terraform plan dry-run

- name: cloudformation
  display_name: "AWS CloudFormation"
  description: "AWS-native IaC. Used when Terraform doesn't support a needed AWS resource."
  validator_support: partial  # cfn-lint, aws cloudformation validate-template

- name: arm_template
  display_name: "Azure ARM Template"
  description: "Azure-native IaC. Used when Terraform doesn't support a needed Azure resource."
  validator_support: partial  # az deployment validate

- name: gcp_deployment_manager
  display_name: "GCP Deployment Manager"
  description: "GCP-native IaC. Used when Terraform doesn't support a needed GCP resource."
  validator_support: partial  # gcloud deployment-manager preview

- name: cli_scripts
  display_name: "CLI scripts"
  description: "aws/az/gcloud/gh CLI invocations. Last-resort when no IaC supports the resource."
  validator_support: minimal  # shellcheck only

- name: manual
  display_name: "Manual prerequisite"
  description: "User must do this themselves; declared as a prereq with check command."
  validator_support: none

- name: mixed
  display_name: "Mixed mechanisms"
  description: "Multiple mechanisms used within the same phase or lab; per-resource declaration governs."
  validator_support: per-resource
```

### 7.5 `lab_credentials`

**Path:** `registry/lab_credentials.yaml`
**Closed enum, maintainer-curated.** Per `schema.md §4.11`, `validation.md §6.8`.

The canonical fake-credential patterns the Generator may plant in lab content and the Validator (Layer 5) whitelists.

```yaml
- id: aws_test_access_key
  platform: aws
  description: "Canonical fake AWS access key used in AWS examples."
  pattern: "^AKIAIOSFODNN7EXAMPLE$"
  example: "AKIAIOSFODNN7EXAMPLE"
  whitelist_rationale: "Documented AWS example access key (in the AWS CLI documentation); never a real credential."

- id: aws_test_secret_key
  platform: aws
  description: "Canonical fake AWS secret key, paired with the access key above."
  pattern: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
  example: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
  whitelist_rationale: "Documented AWS example secret key; never a real credential."

- id: github_test_pat
  platform: github
  description: "Canonical fake GitHub PAT prefix for examples."
  pattern: "^ghp_test_"
  example: "ghp_test_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
  whitelist_rationale: "The `ghp_test_` prefix is a project convention for planted fake PATs; real GitHub PATs start with `ghp_` followed by non-`test_` characters."

- id: azure_test_tenant
  platform: azure
  description: "Canonical fake Azure tenant GUID (all-zeros)."
  pattern: "^0{8}-0{4}-0{4}-0{4}-0{12}$"
  example: "00000000-0000-0000-0000-000000000000"
  whitelist_rationale: "All-zeros GUID is the documented Azure placeholder pattern; production tenant IDs are never all-zeros."

- id: gcp_test_service_account_email
  platform: gcp
  description: "Canonical fake GCP service account email pattern."
  pattern: "@example\\.iam\\.gserviceaccount\\.com$"
  example: "lab-test@example.iam.gserviceaccount.com"
  whitelist_rationale: "The `example.iam.gserviceaccount.com` domain is project-reserved; real GCP service accounts use real project IDs."

- id: npm_test_token
  platform: npm
  description: "Canonical fake npm token prefix for examples."
  pattern: "^npm_TEST_"
  example: "npm_TEST_EXAMPLE_DO_NOT_USE"
  whitelist_rationale: "The `npm_TEST_` prefix is a project convention for planted fakes; real npm tokens start with `npm_` followed by non-`TEST_` characters."

- id: example_uuid
  platform: generic
  description: "Canonical placeholder UUID for examples."
  pattern: "^0{8}-0{4}-0{4}-0{4}-0{12}$"
  example: "00000000-0000-0000-0000-000000000000"
  whitelist_rationale: "All-zeros UUID is a widely-used documented placeholder; production UUIDs are never all-zeros."
```

Layer 5's behavior: a string in generated lab content matching a real-credential pattern (e.g., a real `AKIA...` value, a real `ghp_...` token without the `_test_` segment) is a finding. The same string matching one of the whitelist patterns above is benign — the Generator deliberately planted a canonical fake.

### 7.6 `thesis_types`

**Path:** `registry/thesis_types.yaml`
**Open-set in spirit (registry-evolution per `schema.md §4.16`); the v1 seed is the curated walk's enumeration.**

```yaml
- name: ttp_chain
  description: "TTP (Tactic-Technique-Procedure) chain blog — focus on procedures, not specific CVEs."

- name: vulnerability_chain
  description: "A chain of vulnerabilities composed to escalate access."

- name: misconfiguration
  description: "Attack against misconfiguration (rather than CVE-style flaws)."

- name: cloud_provider_flaw
  description: "A flaw in the cloud provider's own systems."

- name: supply_chain_compromise
  description: "Compromise via supply chain (package, CI, build dep, dependency confusion)."

- name: incident_analysis
  description: "Post-incident analysis with affected parties named."

- name: cross_tenant_compromise
  description: "Attacker crosses tenant or account boundaries."

- name: privilege_escalation
  description: "Within-tier escalation as the primary thesis."

- name: persistence_pattern
  description: "The blog's focus is a persistence technique or family."

- name: detection_methodology
  description: "The blog is primarily about how to *detect* a class of attacks."
```

A thesis may carry multiple types (multi-value field per `schema.md §4.8`). The seed list is the v1 starting point per the curated walk; runtime additions grow it via the registry-evolution flow.

---

## 8. What this document does not specify

Deliberately out of scope, deferred to other places:

- **The full universe of value_types.** This is open-set on purpose. The seed represents what ships, not what the system will accumulate.
- **The exact JSON Schemas of API response shapes** (e.g., `nvd_cve_response_v2`). These belong with the adapter implementation in `cyberlab_gen/external_data_sources/` and are not appropriate for a registry document.
- **Layer 5 pattern rules beyond `lab_credentials`.** Real-credential pattern matching (the `AKIA...` regex applied to outputs, etc.) lives in `validator-rules.md` (planned).
- **The MITRE ATT&CK technique catalog itself.** The bundled MITRE reference at `registry/mitre-attack/` is downloaded JSON, not authored content.

---

## 9. Summary

The v1 seed across all registries:

| Registry | Count | Category |
|---|---|---|
| `value_types` | ~75 | Open-set, extractor-extensible |
| `facets` | ~50 | Open-set, split authority |
| `external_data_sources` | 10 | Closed, maintainer-only |
| `static_catalogs` | 3 | Closed, maintainer-only |
| `execution_contexts` | 8 | Open-set, planner-extensible |
| `detection_components` | 10 | Closed enum |
| `severity_levels` | 4 | Closed enum |
| `detection_formats` | 4 | Closed enum |
| `provisioning_mechanisms` | 7 | Closed enum |
| `lab_credentials` | 7 | Closed, maintainer-curated |
| `thesis_types` | 10 | Open-set in spirit, curated seed |
| **Total** | **~188** | |

The open-set registries (`value_types`, `facets`, `execution_contexts`) ship with seed entries adequate for the curated blog set's coverage. They grow at runtime via the proposal lifecycle in `schema.md §4.16`. The closed registries ship complete in v1; adding entries requires maintainer PR and (for `external_data_sources`) typically code changes.

The seed counts here are the *starting points*. The architecture is designed to handle gaps via overlay growth, not by frontloading exhaustive enumeration. A user who runs cyberlab-gen on twenty curated blogs over a year will have an overlay that meaningfully exceeds the seed — and that's the system working as designed.

---

*End of registry-details document. See `schema-details.md` for the Pydantic shapes of registry entries, `schema.md §4.16` for the proposal lifecycle, and `validation.md §6` for how validator layers consume registry data.*
