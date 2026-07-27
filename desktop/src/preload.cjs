const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("cutroomDesktop", {
  bootstrap: () => ipcRenderer.invoke("desktop:bootstrap"),
  selectMedia: (defaultPath) =>
    ipcRenderer.invoke("desktop:select-media", defaultPath),
  selectFolder: () => ipcRenderer.invoke("desktop:select-folder"),
  revealOutput: (outputPath) =>
    ipcRenderer.invoke("desktop:reveal-output", outputPath),
});
