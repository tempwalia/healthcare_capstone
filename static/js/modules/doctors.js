import { createResourceModule } from "../resource.js";

// Mirrors the specialties/flavor already seeded in the mock provider
// directory (mock_systems/provider_directory_mock/main.py) so sample doctors
// created here feel consistent with the AI recommendation step's candidates,
// even though that mock's doctor_id space is separate from our own table.
const SAMPLE_DOCTORS = [
  { first_name: "Priya", last_name: "Rao", specialization: "Orthopedics", department: "Orthopedic Surgery" },
  { first_name: "Daniel", last_name: "Kim", specialization: "Orthopedics", department: "Sports Medicine" },
  { first_name: "Maria", last_name: "Chen", specialization: "Cardiology", department: "Cardiovascular Care" },
  { first_name: "James", last_name: "Okoye", specialization: "Cardiology", department: "Cardiovascular Care" },
  { first_name: "Lena", last_name: "Novak", specialization: "Dermatology", department: "Dermatology" },
  { first_name: "Omar", last_name: "Farouk", specialization: "Orthopedics", department: "Orthopedic Surgery" },
  { first_name: "Alicia", last_name: "Torres", specialization: "Neurology", department: "Neurology" },
  { first_name: "Samuel", last_name: "Whitfield", specialization: "Family Medicine", department: "Primary Care" },
];

// Kept in lockstep with patients.js's own SAMPLE_LOCATIONS cities so sample
// doctors and patients actually land in the same cities for the
// same-city-priority doctor recommendation to have something to match on.
const SAMPLE_CITIES = ["Springfield", "Riverside", "Fairview", "Lakeside"];

function randomPhone() {
  const n = () => Math.floor(Math.random() * 900 + 100);
  return `+1${n()}${n()}${String(Math.floor(Math.random() * 9000) + 1000)}`;
}

function sampleDoctorData() {
  const pick = SAMPLE_DOCTORS[Math.floor(Math.random() * SAMPLE_DOCTORS.length)];
  const stamp = Date.now().toString().slice(-6);
  return {
    ...pick,
    email: `dr.${pick.last_name.toLowerCase()}.${stamp}@hospital.example`,
    phone: randomPhone(),
    license_number: `MD${stamp}`,
    years_of_experience: Math.floor(Math.random() * 25) + 2,
    bio: `Board-certified ${pick.specialization.toLowerCase()} specialist.`,
    certifications: `Board Certified — ${pick.specialization}`,
    languages_spoken: "English",
    ratings: Math.floor(Math.random() * 2) + 4,
    city: SAMPLE_CITIES[Math.floor(Math.random() * SAMPLE_CITIES.length)],
  };
}

const editFields = [
  { name: "first_name", label: "First Name", type: "text", required: true },
  { name: "last_name", label: "Last Name", type: "text", required: true },
  { name: "email", label: "Email", type: "email" },
  { name: "phone", label: "Phone", type: "text" },
  { name: "specialization", label: "Specialization", type: "text" },
  { name: "years_of_experience", label: "Years of Experience", type: "number" },
  { name: "department", label: "Department", type: "text" },
  { name: "city", label: "City", type: "text", hint: "Used to prioritize this doctor for patients in the same city." },
  { name: "bio", label: "Bio", type: "textarea" },
  { name: "certifications", label: "Certifications", type: "textarea" },
  { name: "languages_spoken", label: "Languages Spoken", type: "text" },
  { name: "ratings", label: "Ratings (1-5)", type: "number" },
  { name: "profile_picture_url", label: "Profile Picture URL", type: "text" },
];

export default createResourceModule({
  key: "doctors",
  title: "Doctors",
  singular: "Doctor",
  basePath: "/doctors",
  // Read is open to every authenticated user (non-PHI directory) — only
  // create/update/delete are gated, behind doctor:manage.
  permissions: { create: "doctor:manage", update: "doctor:manage", delete: "doctor:manage" },
  searchableFields: ["first_name", "last_name", "specialization", "email", "department"],
  columns: [
    { key: "id", label: "ID" },
    { key: "name", label: "Name", format: (r) => `${r.first_name} ${r.last_name}` },
    { key: "specialization", label: "Specialization" },
    { key: "department", label: "Department" },
    { key: "city", label: "City" },
    { key: "phone", label: "Phone" },
    { key: "years_of_experience", label: "Experience (yrs)" },
  ],
  fields: [
    { name: "first_name", label: "First Name", type: "text", required: true },
    { name: "last_name", label: "Last Name", type: "text", required: true },
    { name: "email", label: "Email", type: "email" },
    { name: "phone", label: "Phone", type: "text" },
    { name: "specialization", label: "Specialization", type: "text", required: true },
    { name: "license_number", label: "License Number", type: "text", required: true },
    { name: "years_of_experience", label: "Years of Experience", type: "number" },
    { name: "department", label: "Department", type: "text" },
    { name: "city", label: "City", type: "text", hint: "Used to prioritize this doctor for patients in the same city." },
    { name: "bio", label: "Bio", type: "textarea" },
    { name: "certifications", label: "Certifications", type: "textarea" },
    { name: "languages_spoken", label: "Languages Spoken", type: "text" },
    { name: "ratings", label: "Ratings (1-5)", type: "number" },
    { name: "profile_picture_url", label: "Profile Picture URL", type: "text" },
  ],
  // license_number can't be changed via DoctorUpdate — excluded here.
  editFields,
  sampleData: sampleDoctorData,
});
