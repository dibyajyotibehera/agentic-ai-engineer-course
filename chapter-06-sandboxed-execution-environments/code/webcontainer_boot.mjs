// WebContainers example: run agent-generated Node.js code in the browser.
//
// WebContainers are a WebAssembly-based operating system that runs a real
// Node.js runtime entirely inside a browser tab — no server, no virtual
// machine, no container on your infrastructure. The security boundary is
// the browser's own sandbox. See https://webcontainers.io/api
//
// Requirements (from the WebContainer API documentation):
//   - Node.js only: this runs JavaScript/TypeScript, not Python.
//   - The page that hosts this code must send cross-origin isolation
//     headers, or SharedArrayBuffer is unavailable and boot fails:
//       Cross-Origin-Embedder-Policy: require-corp
//       Cross-Origin-Opener-Policy: same-origin
//   - Install the package first:  npm install @webcontainer/api
//
// This file is a browser example, not a Node.js script: run it inside a
// web page (for example with Vite), not with `node webcontainer_boot.mjs`.

import { WebContainer } from '@webcontainer/api';

// Boot one container. Only a single instance can be booted at a time;
// call webcontainer.teardown() before booting another.
const webcontainer = await WebContainer.boot();

// Mount the agent-generated code as the container's filesystem.
// Nothing outside this tree is visible to the code.
await webcontainer.mount({
  'agent-code.js': {
    file: {
      contents: 'console.log("hello from inside the WebContainer");',
    },
  },
  'package.json': {
    file: {
      contents: JSON.stringify({ type: 'module' }),
    },
  },
});

// Spawn a process inside the container and stream its output back.
const process = await webcontainer.spawn('node', ['agent-code.js']);
process.output.pipeTo(
  new WritableStream({
    write(data) {
      console.log('[sandbox]', data);
    },
  }),
);
const exitCode = await process.exit;
console.log('agent code exited with', exitCode);

// Destroy the container. When the tab closes the environment is gone
// anyway, but teardown releases resources and lets you boot a fresh one.
webcontainer.teardown();
