# 📸 Instagram Analyzer

A Python-based utility to analyze Instagram profiles, inspect media assets, apply AI filters, and automate testing.

---

## 🚀 Key Features

* **Authentication & Profile Handling:** Streamlined session management via `auth.py`.
* **AI Image Filtering:** Smart media processing and filtering with `ai_filter.py`.
* **Automated Audit:** Test runner and workflow inspection using `audit_test.py`.

---

## 🛠️ Architecture & Workflow

```text
[ Instagram Account / Target ]
              │
              ▼
       [ Authentication ] (auth.py)
              │
              ▼
       [ Asset Retrieval ]
              │
              ▼
       [ AI Processing / Filter ] (ai_filter.py)
              │
              ▼
     [ Results & Audit Verification ] (audit_test.py)
