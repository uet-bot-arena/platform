// Practice jobs and all account/management screens remain private.
export function publicRoute(route, ids=[]) {
  return ['home','login','register','recover','guide','competitions','schedule','live','standings'].includes(route)
    || (['arena','replay'].includes(route) && ids[0]!=='practice');
}
