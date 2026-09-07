import type { Metadata } from "next";

import { RegisterForm } from "./register-form";

export const metadata: Metadata = { title: "Create account — AI Assistant for LMS" };

export default function RegisterPage() {
  return <RegisterForm />;
}
