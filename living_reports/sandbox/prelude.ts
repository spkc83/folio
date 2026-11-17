// Runs one snippet of generated code. The process has no Deno permissions.
// The only link to the host is one line of JSON per message on stdin and stdout.
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const stdout = Deno.stdout;
const reader = Deno.stdin.readable.getReader();
let buffer = "";

function send(message: unknown): void {
  stdout.writeSync(encoder.encode(JSON.stringify(message) + "\n"));
}

async function readLine(): Promise<string> {
  while (!buffer.includes("\n")) {
    const { value, done } = await reader.read();
    if (done) throw new Error("The host closed the channel.");
    buffer += decoder.decode(value);
  }
  const end = buffer.indexOf("\n");
  const line = buffer.slice(0, end);
  buffer = buffer.slice(end + 1);
  return line;
}

const start = JSON.parse(await readLine());
let nextId = 0;

async function call(tool: string, args: unknown): Promise<unknown> {
  const id = ++nextId;
  send({ type: "call", id, tool, args, nonce: start.nonce });
  const reply = JSON.parse(await readLine());
  if (reply.error) throw new Error(reply.error);
  return reply.result;
}

const tools = Object.fromEntries(
  (start.tools as string[]).map((name) => [name, (args: unknown) => call(name, args)]),
);

console.log = (...parts: unknown[]) => send({ type: "log", text: parts.map(String).join(" ") });

try {
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  await new AsyncFunction("tools", start.code)(tools);
  send({ type: "done" });
} catch (error) {
  send({ type: "error", text: String(error) });
}
Deno.exit(0);
