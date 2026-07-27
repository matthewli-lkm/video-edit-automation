import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repositoryRoot = path.dirname(desktopRoot);
const target = process.argv.includes("--dmg") ? "dmg" : "dir";

function run(command, args, cwd) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      env: {
        ...process.env,
        CSC_IDENTITY_AUTO_DISCOVERY: "false",
      },
      shell: false,
      stdio: "inherit",
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve();
      } else {
        reject(
          new Error(
            `${command} failed ${signal ? `with ${signal}` : `with code ${code}`}`,
          ),
        );
      }
    });
  });
}

await run("npm", ["run", "build"], path.join(repositoryRoot, "frontend"));
await run("bash", ["scripts/build-backend.sh"], desktopRoot);
await run(
  path.join(desktopRoot, "node_modules", ".bin", "electron-builder"),
  ["--mac", target, "--publish", "never"],
  desktopRoot,
);
