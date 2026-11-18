// Typed client for the ScholarMatch API. Types come from openapi.json (`npm run gen:api`), so a route or schema
// change in scholarmatch/api shows up here as a type error instead of a runtime surprise.
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Profile = Schemas["StudentProfile"];
export type ProfileIn = Schemas["ProfileIn"];
export type ProfileOut = Schemas["ProfileOut"];
export type MatchResult = Schemas["MatchResult"];
export type RunView = Schemas["RunView"];
export type Job = Schemas["Job"];
export type SavedOut = Schemas["SavedOut"];
export type Meta = Schemas["Meta"];
export type Health = Schemas["Health"];

export const api = createClient<paths>({ baseUrl: "" });

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** FastAPI errors are `{detail: string}` or, for validation, `{detail: [{loc, msg}, ...]}`. */
function message(error: unknown, status: number): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".") || "request"}: ${d.msg}`)
      .join("; ");
  return `Request failed (${status}).`;
}

/** Turn an openapi-fetch result into its data, or throw an ApiError with the server's message. */
export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call;
  if (error !== undefined || !response.ok) throw new ApiError(response.status, message(error, response.status));
  return data as T;
}
