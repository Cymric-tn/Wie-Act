import { io } from "socket.io-client";
import { SERVER_URL } from "./lib/api";

export const socket = io(SERVER_URL, {
  autoConnect: true
});
