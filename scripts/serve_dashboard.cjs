// Serve only the dashboard's public assets; never expose the repository directory.
const http = require("node:http");
const { readFile } = require("node:fs/promises");
const path = require("node:path");

const publicFiles = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/index.html", ["index.html", "text/html; charset=utf-8"]],
  ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/styles.css", ["styles.css", "text/css; charset=utf-8"]],
]);
const port = Number(process.env.PORT || 4173);
const server = http.createServer(async (request, response) => {
  if (!["GET", "HEAD"].includes(request.method)) {
    response.writeHead(405, { Allow: "GET, HEAD" });
    response.end();
    return;
  }
  const asset = publicFiles.get(request.url.split("?", 1)[0]);
  if (!asset) {
    response.writeHead(404);
    response.end("Not found");
    return;
  }
  try {
    const data = await readFile(path.join(__dirname, "..", asset[0]));
    response.writeHead(200, {
      "Content-Type": asset[1],
      "Content-Length": data.length,
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    response.end(request.method === "HEAD" ? undefined : data);
  } catch {
    response.writeHead(500);
    response.end("Could not load dashboard asset");
  }
});
server.on("error", (error) => {
  console.error(`Dashboard server failed: ${error.code}`);
  process.exitCode = 1;
});
server.listen(port, "127.0.0.1", () => {
  console.log(`Ledgerly preview: http://127.0.0.1:${server.address().port}`);
});
