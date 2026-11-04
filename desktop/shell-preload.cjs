const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('formforgeDesktop',Object.freeze({
  command: c=>ipcRenderer.invoke('desktop:command',c),
  layout: data=>ipcRenderer.invoke('desktop:layout',data),
  status: ()=>ipcRenderer.invoke('desktop:status'),
  onStatus: callback=>ipcRenderer.on('desktop:status',(_event,status)=>callback(status)),
}));
