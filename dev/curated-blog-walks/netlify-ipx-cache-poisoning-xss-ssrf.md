# Blog walk: `netlify-ipx-cache-poisoning-xss-ssrf`

> **Ground-truth reference — human-reviewed (2026-06-20).** This walk was
> agent-drafted from the Netlify advisory (with GHSA + Sam Curry corroboration),
> then reviewed against the source in the human ground-truth pass and corrected;
> it now stands as **blessed ground truth** for Extractor/Planner scoring.
> Distinct gate: the eval **calibration values** (CALIBRATION.md) remain pending
> the separate paid `--stage plan` run — the human-pass gate is cleared, the
> calibration gate is not (ADR 0104).

A manual ground-truth reading of a curated blog, mapping the blog's narrative
onto the AttackSpec structure (`docs/schema.md §4.8`). This entry's distinctive
coverage role is the **`runtime:*` Planner-proposal trigger**: the lab would
provision against Netlify, which is *not* one of the first-class runtimes, so the
Planner must propose a facet the registry lacks (see §6.2 and §12).

---

## 1. Header

- **id:** `netlify-ipx-cache-poisoning-xss-ssrf` (matches `eval/blog-sets/manifest.yaml`)
- **shape:** `supply_chain`
- **URL:** https://www.netlify.com/blog/netlify-ipx-vulnerability/
- **Canonical URL:** https://www.netlify.com/blog/netlify-ipx-vulnerability/
- **Title:** "Netlify IPX Vulnerability"
- **Publisher:** Netlify
- **Publisher kind:** `vendor_advisory` (a vendor's own security advisory, not a
  research lab's write-up; the technical depth lives in the corroborating
  researcher/GHSA sources — see §15)
- **Authors:** Mark Dorsi (Netlify CISO); the underlying research is Sam Curry's
  (the researcher who discovered and disclosed the vulnerability)
- **Publication date:** 2022-09-22 (vendor-advisory publication date; the
  disclosure/fix timeline is earlier — report 2022-08-24, patch 2022-08-26 — see §15)
- **Accessed date:** 2026-06-19
- **Content hash:** TBD (the Extractor produces this mechanically)

## 2. One-paragraph summary

A flaw in Netlify's `@netlify/ipx` image-optimization handler — the `/_ipx/`
route used by Next.js sites deployed on Netlify — let an attacker manipulate the
`X-Forwarded-Proto` request header to bypass the source-image domain allowlist
and make the server fetch and return arbitrary external content. Because image
responses were served without a Content-Security-Policy and SVG can embed
JavaScript, a malicious SVG returned this way executes as stored XSS under the
victim site's own trusted origin. The handler also cached responses globally,
keyed on the request URL independently of the attack headers, so a single
poisoned request served the payload to every later visitor — turning a
per-request bug into platform-wide persistent XSS, and the same header trick
yielded a full-response SSRF on any site running netlify-ipx. Tracked as
CVE-2022-39239 / GHSA-9jjv-524m-jm98 and patched in `@netlify/ipx` 1.2.3.

## 3. Thesis

Per `schema.md §4.8` lines 316–333.

- **Types:** `vulnerability_chain`, `supply_chain_compromise`,
  `cloud_provider_flaw`. (All three are defensible; see §15 "SHAPE" for why
  `supply_chain` is the headline framing rather than a generic
  `vulnerability_chain`.)
- **Summary:** A header-injection allowlist bypass in the `@netlify/ipx` image
  handler is chained with the absence of CSP on returned images and a global
  response cache keyed independently of the attack header, turning a single
  crafted request into platform-wide stored XSS and full-response SSRF affecting
  every Next.js site deployed on Netlify with the IPX plugin.
- **Attacker objective:** Achieve stored cross-site scripting executing under a
  victim site's trusted origin (and full-response SSRF) against any site running
  the Netlify IPX image handler, persisted to all visitors via cache poisoning.
- **Vulnerability story:** Substantive. The root cause is improper validation of
  the `X-Forwarded-Proto` header in `@netlify/ipx`. The handler builds the
  upstream image URL roughly as `${protocol}://${host}${id}`, taking `protocol`
  directly from the attacker-controlled header. By supplying a full URL
  terminated with `?` or `#` in that header, the attacker overwrites the entire
  upstream URL before the local/allowlist `isLocal` check effectively constrains
  it, so the server fetches an arbitrary attacker-hosted resource. Two latent
  platform defaults convert this SSRF into a universal XSS: image responses were
  served without a Content-Security-Policy, and SVG is an executable document
  format, so a returned malicious SVG runs script under the site's origin. The
  third leg is the cache: responses are cached globally and indexed on the
  request URI rather than on the attack headers, so one poisoning request serves
  the payload to every later visitor who requests that path without any headers.
  Fixed by sanitizing forwarded headers across Netlify infrastructure, shipping
  header sanitization in `@netlify/ipx` 1.2.3, and adding CSP to image responses.

## 4. Chain steps

Five ordered steps. Step shape mirrors `schema.md §4.8` lines 358–383.

### Chain step 1: Identify the IPX image-optimization endpoint

- **Blog excerpt:**
  > a stored cross-site scripting and full response server-side request forgery on any website running the Netlify IPX image handler
- **Description:** The attacker locates the `/_ipx/` route exposed by sites using
  the `@netlify/ipx` image-optimization plugin (the Netlify-specific image
  handler for Next.js). This route accepts an image identifier and
  proxies/optimizes the source image, and is the entry point for the chain. No
  authentication is required; the endpoint is reachable by any unauthenticated
  visitor.
- **MITRE techniques:** `T1595.003` (active scanning — wordlist scanning), `T1190`
  (exploit public-facing application)
- **Preconditions:** A target Next.js site deployed on Netlify with the
  `@netlify/ipx` plugin (or a self-hosted instance of the handler) exposing the
  `/_ipx/` route.
- **Postconditions:** Attacker holds the reachable IPX endpoint URL.
- **Outputs:** `ipx_endpoint_url`
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** `full` is the top reproducibility tier;
  deploying a Next.js site with the plugin (or running the open-source handler
  locally) deterministically exposes `/_ipx/`, and discovery is a plain,
  fully scriptable HTTP request.

### Chain step 2: Inject a full URL via `X-Forwarded-Proto` to overwrite the upstream URL

- **Blog excerpt:**
  > manipulate the X-Forwarded-Proto header as it is sent to the image handler to bypass the source image allowlist, returning arbitrary images
- **Description:** The handler builds the upstream fetch URL as
  `${protocol}://${host}${id}`, where `protocol` is taken unsanitized from
  `X-Forwarded-Proto` (`event.headers['x-forwarded-proto'] || 'http'`). The
  attacker sets the header to a complete attacker URL terminated with a trailing
  `?` or `#` (e.g. `http://attacker.com/malicious.svg?`), which terminates
  parsing of the original identifier and replaces the whole upstream URL. This is
  the core header-injection primitive.
- **MITRE techniques:** `T1190`, `T1659` (content injection)
- **Preconditions:** Step 1 (reachable `/_ipx/` endpoint on a vulnerable build,
  `@netlify/ipx` < 1.2.3).
- **Postconditions:** Attacker controls the upstream URL the handler will fetch.
- **Outputs:** `crafted_http_request`, `forged_upstream_url`
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Top tier; the vulnerable code path and
  exact header value are documented, and sending a crafted `X-Forwarded-Proto`
  header is a single scriptable HTTP request against a vulnerable build.

### Chain step 3: Bypass the allowlist and fetch an arbitrary external resource (SSRF)

- **Blog excerpt:**
  > By sending specially crafted headers an attacker can bypass the source image domain allowlist, causing the handler to load and return arbitrary images.
- **Description:** Because the overwritten URL is constructed before the
  local/allowlist (`isLocal`) validation effectively constrains it, the allowlist
  is bypassed and the handler issues an outbound request to the attacker host via
  its source-image loader. This is the full-response SSRF leg: the server fetches
  and returns arbitrary remote content, not just allowlisted site images.
- **MITRE techniques:** `T1190`, `T1071.001` (application-layer protocol — web)
- **Preconditions:** Step 2 (a forged upstream URL accepted by the handler).
- **Postconditions:** Server returns the attacker-controlled remote response to
  the requester (SSRF achieved).
- **Outputs:** `ssrf_response`, `arbitrary_remote_image`
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Top tier; the bypass is a direct
  consequence of step 2 and is reproducible end-to-end against the vulnerable
  handler — the attacker controls both the request and the upstream resource.

### Chain step 4: Serve a malicious SVG from the victim origin (stored XSS)

- **Blog excerpt:**
  > a malicious SVG could be returned with an embedded script which would be served from the site domain
- **Description:** The attacker points the forged upstream URL at an
  attacker-hosted SVG containing embedded JavaScript. Because image responses
  were served without a Content-Security-Policy and SVG is an executable document
  type, the SVG is returned through the legitimate `/_ipx/` path under the victim
  site's own origin; when rendered/navigated to, the embedded script executes in
  the trusted origin, yielding cross-site scripting.
- **MITRE techniques:** `T1059.007` (JavaScript), `T1189` (drive-by compromise)
- **Preconditions:** Step 3 (the handler will return arbitrary attacker content),
  plus the two platform defaults (missing CSP; SVG treated as executable).
- **Postconditions:** Script executes under the victim site's origin.
- **Outputs:** `malicious_svg_payload`, `xss_execution`
- **Reproducibility:** `full`
- **Why this tier (not a higher one):** Top tier; hosting a script-bearing SVG and
  confirming it is returned (and executes) via the site origin is fully scriptable
  against a vulnerable build in a lab browser/headless context.

### Chain step 5: Poison the global response cache so the payload reaches all visitors

- **Blog excerpt:**
  > this image will then be served to visitors without requiring those headers to be set
- **Description:** The handler caches responses globally, keyed on the request URI
  rather than on the attack headers. A single crafted request stores the malicious
  SVG against a normal `/_ipx/` path; every subsequent visitor requesting that path
  (with no special headers) is served the cached malicious payload. This converts
  the per-request XSS/SSRF into persistent, platform-wide stored XSS affecting all
  visitors of any site running netlify-ipx.
- **MITRE techniques:** `T1584.006` (compromise infrastructure — web services),
  `T1565.002` (transmitted data manipulation)
- **Preconditions:** Step 4 (a malicious payload served through `/_ipx/`).
- **Postconditions:** The poisoned cache entry serves the payload to later
  visitors with no attacker interaction.
- **Outputs:** `poisoned_cache_entry`, `persistent_stored_xss`
- **Reproducibility:** `partial_simulation`
- **Why this tier (not a higher one):** The cache-poisoning *mechanic* is fully
  reproducible against a single self-provisioned instance — but the blog's claimed
  impact is Netlify's **global, multi-tenant edge cache** serving all visitors of
  all customer sites. A lab can demonstrate poisoning on its own tenant, yet cannot
  safely (or legitimately) reproduce Netlify's production shared cache serving
  other customers; the real-world blast radius is stubbed to a single tenant, so it
  is not `full`. It is more than `demonstration_only` because the mechanic itself
  genuinely runs in the lab.

## 5. Alternative paths (optional)

Not applicable for this blog — the chain is canonical and unbranched. The SSRF
(step 3) and stored-XSS (step 4) are not alternatives to each other; they are
sequential legs of one chain (SSRF is the primitive, the missing-CSP/SVG default
upgrades it to XSS), and the cache-poisoning step makes the same payload
persistent rather than offering a different route.

## 6. Facets

Per `schema.md §4.13`. Walker's best estimate; categories tagged with who is
*architecturally* responsible.

### 6.1 `target:*` (Extractor-derived)

`target:netlify`, `target:nextjs`, `target:ipx_image_handler`,
`target:web_application`. Note the deliberate split: Next.js is the **framework**
the vulnerable handler serves (`target:nextjs` is correct), distinct from the
runtime the lab provisions against (§6.2).

### 6.2 `runtime:*` (Planner-derived; walker's guess)

`runtime:netlify` — **a non-first-class, proposed facet the registry lacks.**

The runtimes the registry **seeds** are `{aws, azure, gcp, github, local}`
(`registry-details.md §3.3` — `aws`/`azure`/`gcp`/`github` are `first_class: true`, `local` is a
seeded `first_class: false` best-effort runtime). Netlify is seeded by none of these, so the Planner must
**propose** `runtime:netlify` (a `first_class: false` facet that resolves in no
registry today). This is the load-bearing coverage role of this blog: it is the
`runtime:*` Planner-proposal trigger. The proposal flows to the post-Planner
interactive interrupt (Task 8, ADR 0100) for Accept/Edit and, if accepted, lands
in the **run-scoped** overlay (`<run_dir>/registry-overlay`), never the shared
production vocabulary.

Caution flagged for the Extractor/Planner (see §15): do **not** emit
`runtime:nextjs` (Next.js is the framework, not a runtime) and do **not** emit
`runtime:vercel` (Vercel is the more famous Next.js host but is not involved in
this CVE at all — the bug is the Netlify-specific `@netlify/ipx` fork).

### 6.3 `lab_class_signal:*` (mostly Extractor-derived; some split)

`lab_class_signal:vulnerability_chain`, `lab_class_signal:supply_chain`. The
single-CVE-in-a-platform-shipped-library framing drives both signals; there is no
`incident_analysis`, `external_channel`, or `produces_world_state` signal here.

## 7. Value types referenced

Per `schema.md §4.12`. None of the value types this blog surfaces are AWS-style
registry primitives; they are descriptive types for a web / edge-PaaS
exploitation domain, all flagged **`(proposed)`**:

- `ipx_endpoint_url` `(proposed)` — the reachable `/_ipx/` route.
- `crafted_http_request` `(proposed)` — the request bearing the malicious
  `X-Forwarded-Proto` header.
- `forged_upstream_url` `(proposed)` — the overwritten upstream fetch URL.
- `ssrf_response` `(proposed)` — the arbitrary remote content returned by the
  server.
- `arbitrary_remote_image` `(proposed)` — the fetched attacker-hosted resource.
- `malicious_svg_payload` `(proposed)` — the script-bearing SVG.
- `xss_execution` `(proposed)` — script execution under the victim origin.
- `poisoned_cache_entry` `(proposed)` — the cached malicious response.
- `persistent_stored_xss` `(proposed)` — the persisted, all-visitor XSS state.

Justification (one sentence): the v1 registry is AWS/cloud-credential-centric, so a
web/edge-PaaS exploitation chain like this surfaces an entirely new descriptive
value-type cluster that registry-evolution would add for this domain.

## 8. Defender techniques

Not applicable for this blog — it is a vulnerability disclosure / vendor advisory
written from the attacker-and-fix perspective, with no defender-investigation
(detection-engineering / threat-hunting / forensic) narrative. Per the template,
defender techniques are populated only for `incident_analysis` blogs.

## 9. Defenses

Controls per `schema.md §4.8` lines 416–426.

- **Description:** Sanitize/strip attacker-controllable forwarded headers
  (`X-Forwarded-Proto`, `X-Forwarded-Host`) at the platform edge before they
  reach the image handler.
  - **Applicability:** `vendor_only` (platform-edge change Netlify shipped).
  - **Addresses chain steps:** 2, 3.
- **Description:** Upgrade `@netlify/ipx` to ≥ 1.2.3, which adds header
  sanitization.
  - **Applicability:** `customer_actionable`.
  - **Addresses chain steps:** 2, 3.
- **Description:** Set a restrictive Content-Security-Policy on
  image-optimization responses to neutralize executable SVG payloads (added
  upstream in IPX).
  - **Applicability:** `architectural_mitigation`.
  - **Addresses chain steps:** 4.
- **Description:** Enforce the source-image domain allowlist *before* constructing
  the upstream URL, and validate that the constructed URL's host is allowlisted —
  do not let header values overwrite the host/protocol.
  - **Applicability:** `architectural_mitigation`.
  - **Addresses chain steps:** 2, 3.
- **Description:** Avoid returning SVG from image optimizers, or serve it with a
  `Content-Type`/`Content-Disposition` that prevents inline script execution.
  - **Applicability:** `architectural_mitigation`.
  - **Addresses chain steps:** 4.
- **Description:** Key the response cache on the full security-relevant request
  context (including any headers that influence the upstream fetch), or never
  cache responses derived from untrusted forwarded headers.
  - **Applicability:** `architectural_mitigation`.
  - **Addresses chain steps:** 5.
- **Description:** Deprecate/retire the un-forked Netlify IPX plugin in favor of
  the maintained, sanitized fork.
  - **Applicability:** `customer_actionable`.
  - **Addresses chain steps:** 1–5 (eliminates the vulnerable handler entirely).

## 10. External references

- **CVEs cited:** `CVE-2022-39239`. (Note: the Netlify blog itself does **not**
  print the CVE; it only links the GHSA. The CVE comes from the linked advisory —
  see §15.)
- **Related blogs / advisories:**
  - GHSA-9jjv-524m-jm98 (GitHub Security Advisory — the authoritative technical record)
  - Sam Curry, "Universal XSS on Netlify's Next.js Library" — https://samcurry.net/universal-xss-on-netlifys-next-js-library/
  - GitHub advisory page — https://github.com/netlify/netlify-ipx/security/advisories/GHSA-9jjv-524m-jm98
  - Upstream IPX project — https://github.com/unjs/ipx
- **MITRE ATT&CK techniques referenced:** none cited as background beyond the
  per-step techniques in §4.

## 11. Real-world incidents

Per `schema.md §4.8` lines 339–356.

- **Status:** `incidents_documented`
- **Evidence source:** Sam Curry's disclosure write-up, which reports the
  vulnerability was demonstrable against high-profile production sites running
  netlify-ipx as proof of widespread exposure.
- **Incidents:** Researcher demonstration during coordinated disclosure — **not**
  evidence of malicious in-the-wild exploitation.
  - **name:** Demonstrated exposure across major netlify-ipx sites
  - **description:** During disclosure, the researcher demonstrated the bug was
    exploitable against multiple high-profile production sites running the
    vulnerable handler, illustrating blast radius.
  - **affected_organizations:** Named examples include Gemini, PancakeSwap,
    Docusign, MoonPay, and Celo.
  - **attribution:** Sam Curry (security researcher), under coordinated
    disclosure — not a malicious actor.
  - **date_range:** circa 2022-08 (disclosure window).

  These are demonstration context, not lab steps (see §15): they are evidence of
  blast radius, and attacking them would be illegal/out-of-scope. No malicious
  in-the-wild exploitation is documented.

## 12. Expected lab class

- **Lab kind:** A `vulnerability_chain` / `supply_chain` web-exploitation lab on a
  **non-first-class edge PaaS (Netlify)** — concretely, "Netlify IPX
  `X-Forwarded-Proto` allowlist-bypass → SSRF → SVG/CSP stored XSS → cache
  poisoning chain on a Next.js-on-Netlify deployment."
- **Why this class:** The `target:netlify` / `target:nextjs` /
  `target:ipx_image_handler` facets plus the `vulnerability_chain` and
  `supply_chain` lab-class signals point at a single-CVE-in-a-platform-library
  chain reproduced by **provisioning a Netlify Next.js site**. That provisioning
  target forces a **`runtime:netlify` Planner proposal that the registry lacks** —
  Netlify is outside the seeded set `{aws, azure, gcp, github, local}`, so
  this is the required **non-first-class runtime trigger**. The blast radius is a
  property of the dependency/supply chain (one library bug → every consuming
  Next.js-on-Netlify site), which is why `supply_chain` is the headline shape over
  a generic `vulnerability_chain` (see §15 "SHAPE").

## 13. Reproducibility (lab-level)

Derived from the per-step `reproducibility` values in §4 under the
any-heterogeneity-mixed rule (`schema.md §4.8` line 438).

- **Classification (lab-level):** `mixed`
- **Caveats:** All required steps 1–4 are individually `full` and reproducible
  against a self-provisioned vulnerable instance of `@netlify/ipx` (< 1.2.3) on
  Netlify or a local clone of the handler. Step 5 (cache poisoning) is
  `partial_simulation`: the poisoning mechanic is reproducible on a single tenant,
  but the blog's headline impact — Netlify's **global, multi-tenant edge cache**
  serving the payload to all visitors of all affected customer sites — depends on
  Netlify's production shared cache and cannot be safely or legitimately staged.
  The vulnerability is fixed in production (patched 1.2.3 + infra header
  sanitization), so faithful reproduction requires **pinning an old
  `@netlify/ipx` version**; current Netlify will sanitize the forwarded headers.
- **Derivation trace:** Step 1 = `full`, Step 2 = `full`, Step 3 = `full`,
  Step 4 = `full`, Step 5 = `partial_simulation`. No step is `not_reproducible`,
  so nothing is excluded as non-required. Tiers present across required steps:
  `{full, partial_simulation}` → ≥ 2 distinct tiers → `mixed`.
- **Overall assessment:** A capable lab can fully reproduce the core chain
  (endpoint discovery → `X-Forwarded-Proto` injection → allowlist bypass/SSRF →
  SVG-served-from-origin XSS) by provisioning a pre-1.2.3 instance, achieving
  genuine stored XSS and SSRF against its own tenant; only the platform-wide
  global-cache blast radius must be simulated against a single tenant.

## 14. Coverage tags

Which `eval.md §7.3` coverage dimensions this blog satisfies (drives
`coverage_tags:` in `eval/blog-sets/manifest.yaml`):

- `non_first_class_runtime`
- `runtime:netlify`
- `mixed_reproducibility`
- `platform:netlify`
- `vulnerability_disclosure`
- `supply_chain`
- `complexity:medium` (5 chain steps, in the 4–8 band)
- `thesis:vulnerability_chain`
- `thesis:cache_poisoning`
- `thesis:ssrf`
- `thesis:stored_xss`
- `edge_paas`
- `nextjs`
- `web`

## 15. Manual ground-truth notes

**The highest-value section.** What a careful reader noticed that an LLM
Extractor on the same source might miss.

- **Excerpt provenance (disclosed honestly):** The chosen URL is Netlify's own
  **vendor advisory**, which is thin and PR-flavored ("User Action Required:
  None"). The technical meat lives in **Sam Curry's write-up (samcurry.net)** and
  the **GHSA advisory (GHSA-9jjv-524m-jm98)**. The per-step verbatim excerpts in
  §4 are drawn from the Netlify advisory where its text was specific enough, and
  from the GHSA impact line (step 1's excerpt) where the Netlify text was too
  vague — these are **cited corroborating sources, attributed honestly**, not the
  primary blog. All three sources corroborate the same chain, so there is no
  conflict between them; the walk simply quotes whichever source stated a given
  leg most precisely.

- **Runtime vs. target distinction (the load-bearing call):** Netlify is
  genuinely the **provisioning/runtime** target, not just a victim brand —
  reproducing the attack requires deploying a Next.js site with the
  `@netlify/ipx` plugin onto Netlify (or running the handler standalone), and the
  bug lives in Netlify's platform-shipped library and edge cache. So
  `runtime:netlify` is correct, and it is **outside** the seeded set
  `{aws, azure, gcp, github, local}` → a true non-first-class runtime trigger the
  registry lacks. An LLM could be fooled into emitting `runtime:nextjs` (Next.js
  is the *framework* — `target:nextjs` is fine, `runtime:nextjs` is wrong) or
  `runtime:vercel` (the more famous Next.js host, but **not involved in this CVE
  at all** — the bug is the Netlify-specific fork). Do not let "Next.js" pull the
  runtime toward Vercel.

- **Missing information / where the CVE comes from:** The Netlify blog does **not**
  print a CVE; it only links the GHSA. `CVE-2022-39239` comes from the GHSA page.
  An Extractor reading *only* the blog would miss the CVE — the authoritative
  source is the linked advisory.

- **Date ambiguity:** The vendor blog is dated 2022-09-22 (publication), but the
  disclosure timeline is earlier (report 2022-08-24, fix 2022-08-26). The walk
  uses the blog publication date for the header and records the real disclosure/fix
  dates in §2/§3 so they are not lost. Do not conflate the blog date with the
  patch date.

- **Mechanism subtlety (the likely Extractor error):** The three legs are easy to
  collapse into one "XSS bug." They are distinct and each is necessary: (a) the
  header-injection allowlist bypass → SSRF (the primitive); (b) missing-CSP +
  SVG-is-executable → the SSRF becomes XSS under the site origin; (c) the global
  cache keyed independently of the attack header → per-request XSS becomes
  *persistent* stored XSS for all visitors. A weaker model tends to say "cache
  poisoning causes XSS" and drop the CSP/SVG leg, or drop the SSRF entirely. This
  walk keeps SSRF (step 3) and the SVG/CSP leg (step 4) as separate
  value-producing steps. The exact URL-overwrite trick — a full URL ending in `?`
  or `#` in `X-Forwarded-Proto` so it overwrites `${protocol}://${host}${id}` — is
  the concrete detail an Extractor should **not** paraphrase away.

- **Reproducibility judgment (the call made):** Steps 1–4 are `full` because they
  are deterministic HTTP interactions against a vulnerable build the lab can
  self-provision (pin `@netlify/ipx` < 1.2.3). Step 5 is deliberately
  `partial_simulation` — **not** because the cache-poisoning code can't run, but
  because the *blog's claim* is global/multi-tenant impact ("served to all
  visitors of all sites"). A lab can poison its own single tenant's cache (real)
  but cannot and must not reproduce Netlify's production shared edge cache serving
  other customers' visitors. That single mixed step drives the lab-level class to
  `mixed`. Two wrong answers to watch: over-rating step 5 as `full` by ignoring the
  multi-tenant scope, or under-rating the whole lab as `demonstration_only` by
  assuming a patched CVE is unreproducible (it **is** reproducible with version
  pinning). `mixed` is the calibrated answer.

- **`not_reproducible` exclusion:** No step is `not_reproducible`, so the
  any-heterogeneity exclusion rule removes nothing here.

- **Real-world victims are not chain steps:** The named sites (Gemini,
  PancakeSwap, Docusign, MoonPay, Celo) are demonstration context from Sam Curry,
  captured under §11 real-world incidents — **not** lab steps. Do not turn
  "attacker demonstrated against Docusign" into a chain step; attacking them would
  be illegal/out-of-scope, and they are evidence of blast radius, not reproducible
  actions.

- **Value types:** All proposed value types (§7) are descriptive/new for a
  web/edge-PaaS exploitation domain; none are AWS-credential-style registry
  primitives. Treat them as proposed types the registry would add for this domain.

- **Shape call:** `supply_chain` was chosen over a generic `vulnerability_chain`
  label because the defect ships in a platform/package (`@netlify/ipx`) consumed
  transitively by every Next.js-on-Netlify deployment — the blast radius is a
  property of the dependency/supply chain (one library bug → every consuming site).
  It is equally defensible as a `cloud_provider_flaw` (Netlify's edge/cache
  defaults are part of the chain); both are listed in `thesis.types` (§3).

- **Phase 1 prompt-engineering hints:** (1) The prompt should force a
  framework-vs-runtime disambiguation — "the host platform the lab provisions
  against is the runtime; the application framework it serves is a target" — to
  stop `runtime:nextjs`/`runtime:vercel` leakage. (2) It should instruct the
  Extractor to pull CVE identifiers from linked advisories, not just the blog body,
  since vendor advisories routinely omit the CVE they were assigned. (3) It should
  resist collapsing multi-leg chains: a single "stored XSS" headline here is
  actually SSRF + missing-CSP/SVG + cache-poisoning, each a necessary, distinct,
  value-producing step.
