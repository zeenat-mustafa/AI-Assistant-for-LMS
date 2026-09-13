import { notFound } from "next/navigation";

import { StudentChat } from "./student-chat";

/** Server Component purely to unwrap the dynamic segment, mirroring the session page. */
export default async function StudentChatPage(
  props: PageProps<"/student/sessions/[id]/chat">,
) {
  const { id } = await props.params;
  const sessionId = Number(id);
  if (!Number.isInteger(sessionId) || sessionId <= 0) notFound();

  return <StudentChat sessionId={sessionId} />;
}
