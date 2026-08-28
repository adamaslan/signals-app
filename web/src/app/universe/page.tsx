import { Suspense } from "react";
import { UniversePageClient } from "./_client";

/**
 * A single static page for local universes (`/universe/`). Reads `?id=N`
 * from the query string; with no id it renders the list, with an id it
 * renders that universe's editor. Same query-param approach the signal page
 * uses to stay compatible with `output: export` — every universe resolves
 * to the same prerendered HTML.
 */
export default function UniversePage() {
  return (
    <Suspense>
      <UniversePageClient />
    </Suspense>
  );
}
