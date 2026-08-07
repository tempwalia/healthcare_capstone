# Sample referral documents

Ready-to-upload test files for the referral workflow's document-intake step
(`app/agents/nodes/intake.py`). Every referral requires exactly two document
types before it can proceed past intake:

1. A **referral letter** (filename must contain `referral` or `letter`)
2. A **recent imaging or lab report** (filename must contain `mri`, `x-ray`,
   `xray`, `imaging`, `lab`, `labs`, `scan`, `ultrasound`, or `radiology`)

Detection is by **filename only**, not file content — these filenames are
already written to match, so uploading them as-is works. Both `.txt` and
`.pdf` are extracted for diagnosis/procedure codes; everything else is
accepted but contributes no extracted codes.

## What's here

| Specialty | Referral letter | Imaging / lab report |
|---|---|---|
| Orthopedics (low back pain, M5x codes) | `orthopedics_referral_letter.txt` | `orthopedics_mri_imaging_report.txt` |
| Cardiology (chest pain, I2x codes) | `cardiology_referral_letter.txt` | `cardiology_lab_results.txt` |
| Dermatology (rash + lesion, L3x/L4x codes) | `dermatology_referral_letter.txt` | `dermatology_skin_scan_report.txt` |

There's also `sample_prescription.txt` — an optional extra file (not one of
the two required types above, and its filename doesn't match either
keyword list, so it contributes no extraction on its own). It's there for
uploading alongside a required pair once a referral reaches the point
where a specialist has completed the consult, so the Documents tab has a
realistic prescription on file for that step of testing.

Each pair includes real-looking ICD-10 diagnosis codes and CPT procedure
codes in the text, so both the LLM extraction path and the regex fallback
(`app/agents/nodes/intake.py::regex_extract_icd10`/`regex_extract_cpt` — this
is the path that runs when no LLM is configured) pick up something
meaningful. The diagnosis code's first letter also drives which specialty
`specialist_node` recommends (`M` → Orthopedics, `I` → Cardiology, `L` →
Dermatology), so each pair should route to its matching specialty — useful
for confirming the AI recommendation step is actually working, not just
defaulting.

## How to use them

1. Create (or pick) a patient — use the dashboard's "Fill Sample Data"
   button, or run `uv run python scripts/seed_sample_insurance.py` first so
   existing patients have a realistic mix of eligible/denied insurance
   policies to test both outcomes.
2. Submit a referral for that patient (`+ New Referral` / `+ Request a
   Referral` on the Referrals page) using a **reason that matches the
   specialty** you want to test (e.g. "persistent lower back pain" for the
   orthopedics pair) — the reason text is also used as a specialty-inference
   fallback if no documents are attached yet.
3. On the referral's detail page, open the **Documents** tab and upload one
   file from each column above (letter first or second, order doesn't
   matter) — once both required types are present, the workflow moves on to
   eligibility verification and specialist recommendation automatically.
4. Watch the **Timeline** tab or the live status indicator for progress, and
   the **Workflow State** tab once it reaches `awaiting_specialist_approval`
   to see which specialists got recommended.
