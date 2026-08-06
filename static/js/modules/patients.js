import { createResourceModule } from "../resource.js";
import { formatDate, capitalize } from "../utils.js";

const SAMPLE_ADDRESSES = [
  "142 Maple Street, Springfield", "78 Oak Avenue, Riverside", "215 Birch Lane, Fairview", "56 Cedar Court, Lakeside",
];
const SAMPLE_ALLERGIES = ["None known", "Penicillin", "Peanuts", "Latex", "Sulfa drugs"];
const BLOOD_TYPES = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"];
// Real, MCP-verified policies seeded in the payer mock (mock_systems/payer_mock/main.py) —
// using one of these means the referral eligibility check actually comes back verified/in-network
// instead of needing to invent a number that won't demo correctly.
const VERIFIED_POLICIES = ["ACME-991123", "ACME-778890"];

function randomFrom(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}
function randomDateOfBirth() {
  const start = new Date(1950, 0, 1).getTime();
  const end = new Date(2006, 0, 1).getTime();
  return new Date(start + Math.random() * (end - start)).toISOString().slice(0, 10);
}
function randomPhone() {
  const n = () => Math.floor(Math.random() * 900 + 100);
  return `+1${n()}${n()}${String(Math.floor(Math.random() * 9000) + 1000)}`;
}

function samplePatientData() {
  const stamp = Date.now().toString().slice(-7);
  return {
    email: `patient.${stamp}@example.com`,
    phone: randomPhone(),
    date_of_birth: randomDateOfBirth(),
    gender: randomFrom(["male", "female", "other"]),
    address: randomFrom(SAMPLE_ADDRESSES),
    emergency_contact_name: "Jordan Lee",
    emergency_contact_phone: randomPhone(),
    insurance_provider: "Acme Health",
    insurance_policy_number: randomFrom(VERIFIED_POLICIES),
    allergies: randomFrom(SAMPLE_ALLERGIES),
    blood_type: randomFrom(BLOOD_TYPES),
    preferred_language: "English",
    lifestyle: "Non-smoker, moderate exercise 2-3x/week.",
    family_history: "No significant family history reported.",
  };
}

export default createResourceModule({
  key: "patients",
  title: "Patients",
  singular: "Patient",
  basePath: "/patients",
  permissions: { create: "patient:manage", update: "patient:manage", delete: "patient:manage" },
  searchableFields: ["first_name", "last_name", "email", "phone"],
  columns: [
    { key: "id", label: "ID" },
    { key: "name", label: "Name", format: (r) => `${r.first_name} ${r.last_name}` },
    { key: "date_of_birth", label: "DOB", format: (r) => formatDate(r.date_of_birth) },
    { key: "gender", label: "Gender", format: (r) => capitalize(r.gender) },
    { key: "phone", label: "Phone" },
    { key: "insurance_provider", label: "Insurance" },
  ],
  fields: [
    { name: "first_name", label: "First Name", type: "text", required: true },
    { name: "last_name", label: "Last Name", type: "text", required: true },
    { name: "email", label: "Email", type: "email" },
    { name: "phone", label: "Phone", type: "text" },
    { name: "date_of_birth", label: "Date of Birth", type: "date", required: true },
    { name: "gender", label: "Gender", type: "select", options: ["male", "female", "other"], required: true, numeric: false },
    { name: "address", label: "Address", type: "textarea" },
    { name: "emergency_contact_name", label: "Emergency Contact Name", type: "text" },
    { name: "emergency_contact_phone", label: "Emergency Contact Phone", type: "text" },
    { name: "insurance_provider", label: "Insurance Provider", type: "text" },
    {
      name: "insurance_policy_number", label: "Policy Number", type: "text",
      hint: "Demo policies that pass eligibility verification: ACME-991123 or ACME-778890 — anything else comes back unverified/denied.",
    },
    { name: "allergies", label: "Allergies", type: "textarea" },
    { name: "blood_type", label: "Blood Type", type: "text" },
    { name: "preferred_language", label: "Preferred Language", type: "text" },
    { name: "lifestyle", label: "Lifestyle", type: "textarea" },
    { name: "family_history", label: "Family History", type: "textarea" },
  ],
  sampleData: samplePatientData,
});
