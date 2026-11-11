// Preview accounts: the signed-in profile lives in this browser only until a
// real authentication backend exists. Passwords are never stored.
import { readStored, store } from "./identity";

const KEY = "ednova-user";

export function getUser() {
  try {
    const user = JSON.parse(readStored(KEY, "null"));
    return user && user.name && (user.role === "student" || user.role === "professor") ? user : null;
  } catch {
    return null;
  }
}

export function saveUser(user) {
  store(KEY, JSON.stringify({ name: user.name, email: user.email, role: user.role, plan: user.plan }));
  store("ednova-board-name", user.name);
}

export function signOut() {
  store(KEY, "null");
}
