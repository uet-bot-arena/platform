// Practice jobs and all account/management screens remain private.
export function publicRoute(route, ids=[]) {
  return ['home','login','verify-email','set-password','guest-register','guest-recover','register','recover','guide','competitions','schedule','live','standings','queue','announcements','limits'].includes(route)
    || (['arena','replay'].includes(route) && ids[0]!=='practice');
}
