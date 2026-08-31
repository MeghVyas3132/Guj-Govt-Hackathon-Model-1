// The client half of app/schemas/filters.py. The parameter names here have to match
// `camera_filter` in app/api/v1/routers/cameras.py exactly, because the same query
// string is sent to three endpoints -- the tile source, the list count and the CSV
// export -- and a name that only the map got wrong would show markers the table has
// already filtered out.

export type Filters = {
  statuses: string[];
  cameraTypes: string[];
  departmentIds: string[];
  q: string;
};

export const EMPTY_FILTERS: Filters = {
  statuses: [],
  cameraTypes: [],
  departmentIds: [],
  q: "",
};

export function toQueryString(filters: Filters): string {
  const params = new URLSearchParams();
  // Repeated keys, not comma-joined: FastAPI's `list[...] = Query(...)` reads
  // ?statuses=online&statuses=offline, and would reject "online,offline" as an
  // invalid enum member.
  filters.statuses.forEach((s) => params.append("statuses", s));
  filters.cameraTypes.forEach((t) => params.append("camera_types", t));
  filters.departmentIds.forEach((d) => params.append("department_ids", d));
  if (filters.q) params.set("q", filters.q);
  return params.toString();
}

export function isEmpty(filters: Filters): boolean {
  return (
    filters.statuses.length === 0 &&
    filters.cameraTypes.length === 0 &&
    filters.departmentIds.length === 0 &&
    filters.q === ""
  );
}
