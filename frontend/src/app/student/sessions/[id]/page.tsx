import { notFound } from "next/navigation";

import { StudentSessionDetail } from "./student-session-detail";

/**
 * Server Component purely to unwrap the dynamic segment, mirroring the
 * instructor route: `params` is a Promise here, typed by the generated
 * PageProps helper.
 */
export default async function StudentSessionPage(
  props: PageProps<"/student/sessions/[id]">,
) {
  const { id } = await props.params;
  const sessionId = Number(id);
  if (!Number.isInteger(sessionId) || sessionId <= 0) notFound();

  return <StudentSessionDetail sessionId={sessionId} />;
}
