#!/usr/bin/env bash
# Pinpoint on the team's GPU box, from a laptop. The box runs the console and the agent
# watcher in tmux (gpu.sh in the deployment folder there), so a caller photo is matched on
# the GPU in about a second instead of half a minute on a laptop CPU.
#
#   ./pinpoint-gpu.sh open      tunnel the console to this laptop and open it in the browser
#   ./pinpoint-gpu.sh close     close the tunnel
#   ./pinpoint-gpu.sh status    tunnel, console, agent and GPU
#   ./pinpoint-gpu.sh logs      last lines of the console, agent and index logs on the box
#   ./pinpoint-gpu.sh restart   restart the console and the agent on the box
#   ./pinpoint-gpu.sh ssh       a shell on the box, in the deployment folder
#
# The console listens only on the box's localhost, so it is reached through an SSH tunnel
# and opened as localhost (the Matterport viewer key is tied to that name). The console
# accepts requests only on its own port, so the laptop end of the tunnel is 4175 too:
# stop a local console first.
# Override with PINPOINT_GPU=user@host, PINPOINT_GPU_DIR=/path.
set -euo pipefail

HOST=${PINPOINT_GPU:-ubuntu@192.222.54.123}
DIR=${PINPOINT_GPU_DIR:-/lambda/nfs/phack/pinpoint-live}
REMOTE_PORT=4175
RUN=${XDG_RUNTIME_DIR:-/tmp}
SOCK=$RUN/pinpoint-gpu.sock
PORT_FILE=$RUN/pinpoint-gpu.port
SSH=(ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new
     -o ServerAliveInterval=30 -o ServerAliveCountMax=3)

die() { echo "pinpoint-gpu: $*" >&2; exit 1; }
remote() { "${SSH[@]}" "$HOST" "cd '$DIR' && $*"; }
tunnel_up() { [ -S "$SOCK" ] && ssh -S "$SOCK" -O check "$HOST" 2>/dev/null; }
port_free() { ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

open_console() {
  if tunnel_up; then
    port=$(cat "$PORT_FILE")
  else
    rm -f "$SOCK"
    echo "checking the console and the agent on $HOST ..."
    remote "tmux has-session -t pp-console 2>/dev/null && tmux has-session -t pp-agent 2>/dev/null || ./gpu.sh start" \
      || die "cannot reach $HOST or start the console there"
    remote "for i in \$(seq 30); do curl -sf -o /dev/null http://127.0.0.1:$REMOTE_PORT/live.html && exit 0; sleep 1; done; exit 1" \
      || die "the console on the box does not answer; see: $0 logs"
    port=$REMOTE_PORT
    if ! port_free "$port"; then
      who=$( (ss -ltnpH "sport = :$port" 2>/dev/null || lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null) | head -3)
      die "port $port on this laptop is in use, probably by a local console (stop it with Ctrl+C in its
terminal). The console only answers on its own port, so the tunnel needs $port.
$who"
    fi
    "${SSH[@]}" -f -N -M -S "$SOCK" -o ExitOnForwardFailure=yes \
      -L "127.0.0.1:$port:127.0.0.1:$REMOTE_PORT" "$HOST" || die "could not open the tunnel"
    echo "$port" > "$PORT_FILE"
  fi
  curl -sf -o /dev/null --max-time 15 "http://127.0.0.1:$port/live.html" \
    || die "the tunnel is open but the console does not answer on port $port"
  url="http://localhost:$port/live.html"
  echo "console: $url   (photo matching runs on the box's GPU)"
  echo "close the tunnel with: $0 close"
  if command -v xdg-open >/dev/null; then
    xdg-open "$url" >/dev/null 2>&1 &
  elif command -v open >/dev/null; then
    open "$url"
  fi
}

close_tunnel() {
  if tunnel_up; then
    ssh -S "$SOCK" -O exit "$HOST" 2>/dev/null || true
    echo "tunnel closed (the console keeps running on the box)"
  else
    echo "no tunnel open"
  fi
  rm -f "$SOCK" "$PORT_FILE"
}

status() {
  if tunnel_up; then
    echo "tunnel: open, http://localhost:$(cat "$PORT_FILE")/live.html"
  else
    echo "tunnel: closed"
  fi
  remote "./gpu.sh status; curl -s -o /dev/null -w 'console on the box: HTTP %{http_code}\n' http://127.0.0.1:$REMOTE_PORT/live.html; echo \"code on the box: \$(git log --oneline -1)\""
}

case "${1:-}" in
  open) open_console ;;
  close) close_tunnel ;;
  status) status ;;
  logs) remote "./gpu.sh logs" ;;
  restart) remote "./gpu.sh restart" ;;
  ssh) "${SSH[@]}" -t "$HOST" "cd '$DIR' && exec bash -l" ;;
  *) echo "usage: $0 open|close|status|logs|restart|ssh" >&2; exit 2 ;;
esac
