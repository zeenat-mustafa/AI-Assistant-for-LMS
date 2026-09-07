import { notFound } from "next/navigation";

import { SessionDetail } from "./session-detail";

/**
 * Server Component purely to unwrap the dynamic segment.
 *
 * Per the Next 16 docs, `params` is a Promise here and `PageProps<route>` is
 * the generated helper that types it. Resolving the id here keeps the client
 * component below from having to parse `useParams()` strings itself.
 */
export default async function InstructorSessionPage(
  props: PageProps<"/instructor/sessions/[id]">,
) {
  const { id } = await props.params;
  const sessionId = Number(id);
  if (!Number.isInteger(sessionId) || sessionId <= 0) notFound();

  return <SessionDetail sessionId={sessionId} />;
}
