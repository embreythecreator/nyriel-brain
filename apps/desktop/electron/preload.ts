import { contextBridge, ipcRenderer, webFrame, webUtils } from 'electron'

// Which translucency the OS can back. Asked synchronously because the renderer
// needs it before its first paint, and answered by main because deciding it
// needs `os.release()` — a sandboxed preload may only require electron, events,
// timers and url, so importing node:os here throws before contextBridge runs
// and takes the ENTIRE bridge down with it (window.nyrielDesktop undefined =>
// "Desktop IPC bridge is unavailable"). No reply means no glass, which degrades
// to an ordinary opaque window rather than a page thinned over nothing.
const translucencySupport = ipcRenderer.sendSync('nyriel:translucency:support')
const hudWindowing = ipcRenderer.sendSync('nyriel:hud:windowing')
const hudNativeDrag = hudWindowing?.nativeDrag === true

contextBridge.exposeInMainWorld('nyrielDesktop', {
  glassSupported: translucencySupport?.glass === true,
  translucencySupported: translucencySupport?.translucency === true,
  getConnection: profile => ipcRenderer.invoke('nyriel:connection', profile),
  // Registry-scoped backend resolution: { connectionId, profile } → descriptor.
  getConnectionFor: payload => ipcRenderer.invoke('nyriel:connection:for', payload),
  getProfileRoutes: profiles => ipcRenderer.invoke('nyriel:plugin-profile-routes', profiles),
  revalidateConnection: () => ipcRenderer.invoke('nyriel:connection:revalidate'),
  touchBackend: profile => ipcRenderer.invoke('nyriel:backend:touch', profile),
  getGatewayWsUrl: profile => ipcRenderer.invoke('nyriel:gateway:ws-url', profile),
  // Registry-scoped fresh WS URL: { connectionId, profile } → result shape of
  // getGatewayWsUrl, minted against that connection's backend.
  getGatewayWsUrlFor: payload => ipcRenderer.invoke('nyriel:gateway:ws-url-for', payload),
  // Union agent roster across every registered connection.
  getAgentRoster: () => ipcRenderer.invoke('nyriel:agents:roster'),
  openSessionWindow: (sessionId, opts) => ipcRenderer.invoke('nyriel:window:openSession', sessionId, opts),
  openSessionInTerminal: (sessionId, opts) => ipcRenderer.invoke('nyriel:window:openInTerminal', sessionId, opts),
  openWindow: () => ipcRenderer.invoke('nyriel:window:openInstance'),
  openBrowserWindow: tabId => ipcRenderer.invoke('nyriel:window:openBrowser', tabId),
  onBrowserPopoutClosed: callback => {
    const listener = (_event, tabId) => callback(tabId)
    ipcRenderer.on('nyriel:browser-popout:closed', listener)

    return () => ipcRenderer.removeListener('nyriel:browser-popout:closed', listener)
  },
  claimAmbientCue: key => ipcRenderer.invoke('nyriel:ambient:claim', key),
  wakeIndicator: {
    getState: () => ipcRenderer.invoke('nyriel:wake-indicator:get'),
    setState: state => ipcRenderer.send('nyriel:wake-indicator:set', state),
    onState: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('nyriel:wake-indicator:state', listener)

      return () => ipcRenderer.removeListener('nyriel:wake-indicator:state', listener)
    }
  },
  petOverlay: {
    // Main renderer → main process: window lifecycle + drag. `request` is
    // `{ bounds, screen }`; resolves with the screen bounds it actually used.
    open: request => ipcRenderer.invoke('nyriel:pet-overlay:open', request),
    close: () => ipcRenderer.invoke('nyriel:pet-overlay:close'),
    setBounds: bounds => ipcRenderer.send('nyriel:pet-overlay:set-bounds', bounds),
    setIgnoreMouse: ignore => ipcRenderer.send('nyriel:pet-overlay:ignore-mouse', ignore),
    // Flip the overlay focusable (and focus it) while the composer needs keys.
    setFocusable: focusable => ipcRenderer.send('nyriel:pet-overlay:set-focusable', focusable),
    // Main renderer → overlay (forwarded by main): push the latest pet state.
    pushState: payload => ipcRenderer.send('nyriel:pet-overlay:state', payload),
    // Overlay → main renderer (forwarded by main): pop back in / composer submit.
    control: payload => ipcRenderer.send('nyriel:pet-overlay:control', payload),
    // Overlay subscribes to state pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nyriel:pet-overlay:state', listener)

      return () => ipcRenderer.removeListener('nyriel:pet-overlay:state', listener)
    },
    // Main renderer subscribes to overlay control messages.
    onControl: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nyriel:pet-overlay:control', listener)

      return () => ipcRenderer.removeListener('nyriel:pet-overlay:control', listener)
    }
  },
  // HUD mode: the chrome-free floating chat. A full app renderer (own gateway)
  // sized as a floating bar, so it mounts the real composer. Main owns the
  // window; `onChanged` keeps every window's toggle truthful.
  hud: {
    nativeDrag: hudNativeDrag,
    windowing: {
      clientPlacement: hudWindowing?.clientPlacement !== false,
      controlDrag: hudWindowing?.controlDrag === true,
      nativeDrag: hudNativeDrag,
      solid: hudWindowing?.solid === true,
      workspaceTransfer: hudWindowing?.workspaceTransfer === true
    },
    open: request => ipcRenderer.invoke('nyriel:hud:open', request),
    close: () => ipcRenderer.invoke('nyriel:hud:close'),
    setIgnoreMouse: ignore => ipcRenderer.send('nyriel:hud:ignore-mouse', ignore),
    beginMove: () => ipcRenderer.send('nyriel:hud:begin-move'),
    endMove: () => ipcRenderer.send('nyriel:hud:end-move'),
    moveBy: delta => ipcRenderer.send('nyriel:hud:move-by', delta),
    setWorkspaceTransfer: transferring => ipcRenderer.send('nyriel:hud:workspace-transfer', transferring),
    setBounds: bounds => ipcRenderer.send('nyriel:hud:set-bounds', bounds),
    resetLayout: () => ipcRenderer.invoke('nyriel:hud:reset-layout'),
    // Whether the band covers the window below the bar. Main pairs it with the
    // user's translucency setting to decide the native frost (macOS vibrancy /
    // Windows 11 DWM backdrop) — see hudFrostFor.
    setFrost: showing => ipcRenderer.invoke('nyriel:hud:frost', showing),
    // The HUD tells main which session it is on; main hands that back to the
    // app window when the HUD closes, so the app can re-home onto it.
    setSession: sessionId => ipcRenderer.send('nyriel:hud:session', sessionId),
    onGoto: callback => {
      const listener = (_event, sessionId) => callback(sessionId)
      ipcRenderer.on('nyriel:hud:goto', listener)

      return () => ipcRenderer.removeListener('nyriel:hud:goto', listener)
    },
    onChanged: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('nyriel:hud:changed', listener)

      return () => ipcRenderer.removeListener('nyriel:hud:changed', listener)
    },
    // Linux only, and silent elsewhere: where the cursor is, in page
    // coordinates, or null when it has left the window. Stands in for the
    // mousemove that `setIgnoreMouseEvents(true, { forward: true })` delivers on
    // macOS and Windows but not here.
    onCursor: callback => {
      const listener = (_event, point) => callback(point)
      ipcRenderer.on('nyriel:hud:cursor', listener)

      return () => ipcRenderer.removeListener('nyriel:hud:cursor', listener)
    },
    // Main's game-overlay watch: whether a fullscreen app (a game) is under
    // the HUD, so the renderer can step back to the low-opacity overlay
    // treatment while one owns the screen.
    onGameOverlay: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('nyriel:hud:game-overlay', listener)

      return () => ipcRenderer.removeListener('nyriel:hud:game-overlay', listener)
    }
  },
  // Quick Entry: the global-hotkey mini composer window. Main owns the OS
  // shortcut + the persisted preference; the quick window only captures text
  // and hands it back, and the primary renderer submits it through the normal
  // prompt path.
  quickEntry: {
    getSettings: () => ipcRenderer.invoke('nyriel:quick-entry:settings:get'),
    setSettings: patch => ipcRenderer.invoke('nyriel:quick-entry:settings:set', patch),
    submit: payload => ipcRenderer.send('nyriel:quick-entry:submit', payload),
    dismiss: () => ipcRenderer.send('nyriel:quick-entry:dismiss'),
    // Primary renderer → main → quick window: gateway connection state + the
    // recent-session options the target picker offers. Main caches the latest
    // payload so a freshly spawned quick window starts from truth.
    pushState: payload => ipcRenderer.send('nyriel:quick-entry:state', payload),
    // Quick window subscribes to those pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nyriel:quick-entry:state', listener)

      return () => ipcRenderer.removeListener('nyriel:quick-entry:state', listener)
    },
    // Main → primary renderer: a submit captured by the quick window.
    onSubmit: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nyriel:quick-entry:submit', listener)

      return () => ipcRenderer.removeListener('nyriel:quick-entry:submit', listener)
    },
    // Main → quick window: you were just summoned (reset draft + refocus).
    onShown: callback => {
      const listener = () => callback()
      ipcRenderer.on('nyriel:quick-entry:shown', listener)

      return () => ipcRenderer.removeListener('nyriel:quick-entry:shown', listener)
    }
  },
  getBootProgress: () => ipcRenderer.invoke('nyriel:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('nyriel:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('nyriel:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('nyriel:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('nyriel:connection-config:test', payload),
  // Opt-in OS-keychain encryption for stored gateway secrets (default off —
  // see secret-storage-policy.ts). get never touches the OS keychain.
  getSecretStorageEncryption: () => ipcRenderer.invoke('nyriel:secret-storage:get'),
  setSecretStorageEncryption: (on: boolean) => ipcRenderer.invoke('nyriel:secret-storage:set', on),
  // v2 multi-connection registry: named agent sources (local / remote / cloud / ssh).
  connections: {
    list: () => ipcRenderer.invoke('nyriel:connections:list'),
    save: payload => ipcRenderer.invoke('nyriel:connections:save', payload),
    remove: id => ipcRenderer.invoke('nyriel:connections:remove', id),
    setPrimary: id => ipcRenderer.invoke('nyriel:connections:set-primary', id),
    setLaunchMode: mode => ipcRenderer.invoke('nyriel:connections:set-launch-mode', mode),
    setLastUsed: id => ipcRenderer.invoke('nyriel:connections:set-last-used', id),
    test: id => ipcRenderer.invoke('nyriel:connections:test', id),
    updateManaged: id => ipcRenderer.invoke('nyriel:connections:update-managed', id),
    // Fan out `nyriel update` to every eligible registered connection.
    // Optional excludeIds skips rows the caller updates through another path.
    updateAll: options => ipcRenderer.invoke('nyriel:connections:update-all', options),
    // Registry lifecycle push (main → renderer): a connection was removed or
    // materially edited, so secondaries scoped to it must be disposed (and,
    // for edits, re-dialed at the new target).
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nyriel:connections:changed', listener)

      return () => ipcRenderer.removeListener('nyriel:connections:changed', listener)
    }
  },
  sshConfigHosts: () => ipcRenderer.invoke('nyriel:ssh-config:hosts'),
  sshResolveHost: host => ipcRenderer.invoke('nyriel:ssh-config:resolve', host),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('nyriel:connection-config:probe', remoteUrl),
  oauthLoginConnectionConfig: remoteUrl => ipcRenderer.invoke('nyriel:connection-config:oauth-login', remoteUrl),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('nyriel:connection-config:oauth-logout', remoteUrl),
  // Nyriel Cloud: one portal login powers discovery + silent per-agent sign-in
  // (cloud-auto-discovery Phase 3).
  cloud: {
    status: () => ipcRenderer.invoke('nyriel:cloud:status'),
    login: () => ipcRenderer.invoke('nyriel:cloud:login'),
    logout: () => ipcRenderer.invoke('nyriel:cloud:logout'),
    discover: org => ipcRenderer.invoke('nyriel:cloud:discover', org),
    agentSignIn: dashboardUrl => ipcRenderer.invoke('nyriel:cloud:agent-sign-in', dashboardUrl)
  },
  profile: {
    get: () => ipcRenderer.invoke('nyriel:profile:get'),
    remember: name => ipcRenderer.invoke('nyriel:profile:remember', name),
    set: name => ipcRenderer.invoke('nyriel:profile:set', name)
  },
  api: request => ipcRenderer.invoke('nyriel:api', request),
  notify: payload => ipcRenderer.invoke('nyriel:notify', payload),
  requestMicrophoneAccess: () => ipcRenderer.invoke('nyriel:requestMicrophoneAccess'),
  readWindowBelow: () => ipcRenderer.invoke('nyriel:window:readBelow'),
  readFileDataUrl: filePath => ipcRenderer.invoke('nyriel:readFileDataUrl', filePath),
  readFileDataUrlForAttach: filePath => ipcRenderer.invoke('nyriel:readFileDataUrlForAttach', filePath),
  dataUrlReadMax: {
    get: () => ipcRenderer.invoke('nyriel:data-url-read-max:get'),
    set: maxMb => ipcRenderer.invoke('nyriel:data-url-read-max:set', maxMb)
  },
  readFileText: filePath => ipcRenderer.invoke('nyriel:readFileText', filePath),
  readPluginSource: (filePath: string) => ipcRenderer.invoke('nyriel:readPluginSource', filePath),
  selectPaths: options => ipcRenderer.invoke('nyriel:selectPaths', options),
  selectSavePath: options => ipcRenderer.invoke('nyriel:selectSavePath', options),
  writeClipboard: text => ipcRenderer.invoke('nyriel:writeClipboard', text),
  readClipboard: () => ipcRenderer.invoke('nyriel:readClipboard'),
  saveGatewayFile: payload => ipcRenderer.invoke('nyriel:saveGatewayFile', payload),
  saveImageFromUrl: url => ipcRenderer.invoke('nyriel:saveImageFromUrl', url),
  contextMenuEdit: command => ipcRenderer.invoke('nyriel:context-menu:edit', command),
  contextMenuCopyImage: () => ipcRenderer.invoke('nyriel:context-menu:copy-image'),
  contextMenuSpellcheck: action => ipcRenderer.invoke('nyriel:context-menu:spellcheck', action),
  contextMenuGuestAddWord: payload => ipcRenderer.invoke('nyriel:context-menu:guest-add-word', payload),
  onContextMenuSpellcheck: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nyriel:context-menu-spellcheck', listener)

    return () => ipcRenderer.removeListener('nyriel:context-menu-spellcheck', listener)
  },
  saveImageBuffer: (data, ext) => ipcRenderer.invoke('nyriel:saveImageBuffer', { data, ext }),
  saveClipboardImage: () => ipcRenderer.invoke('nyriel:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('nyriel:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('nyriel:watchPreviewFile', url),
  watchDirectory: dir => ipcRenderer.invoke('nyriel:watchDirectory', dir),
  stopPreviewFileWatch: id => ipcRenderer.invoke('nyriel:stopPreviewFileWatch', id),
  setActiveWork: payload => ipcRenderer.send('nyriel:active-work', payload),
  setTitleBarTheme: payload => ipcRenderer.send('nyriel:titlebar-theme', payload),
  setNativeTheme: mode => ipcRenderer.send('nyriel:native-theme', mode),
  setTranslucency: payload => ipcRenderer.send('nyriel:translucency', payload),
  setKeepAwake: on => ipcRenderer.send('nyriel:keep-awake', on),
  setDisableF12: blocked => ipcRenderer.send('nyriel:devtools:disable-f12', blocked),
  setPreviewShortcutActive: active => ipcRenderer.send('nyriel:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('nyriel:openExternal', url),
  mcpOauth: {
    // One-shot loopback listener for MCP OAuth against remote backends: bind
    // on this machine, hand redirectUri to mcp.servers.oauth.start, then wait
    // for the provider redirect and relay code/state via oauth.callback.
    listen: () => ipcRenderer.invoke('nyriel:mcp-oauth:listen'),
    wait: (id, timeoutMs) => ipcRenderer.invoke('nyriel:mcp-oauth:wait', id, timeoutMs),
    cancel: id => ipcRenderer.invoke('nyriel:mcp-oauth:cancel', id)
  },
  openPreviewInBrowser: url => ipcRenderer.invoke('nyriel:openPreviewInBrowser', url),
  reachPreviewUrl: url => ipcRenderer.invoke('nyriel:preview:reach', url),
  setActiveConnectionRoute: route => ipcRenderer.send('nyriel:connection:active-route', route),
  fetchLinkTitle: url => ipcRenderer.invoke('nyriel:fetchLinkTitle', url),
  resolveFavicon: url => ipcRenderer.invoke('nyriel:resolveFavicon', url),
  sanitizeWorkspaceCwd: cwd => ipcRenderer.invoke('nyriel:workspace:sanitize', cwd),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('nyriel:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('nyriel:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('nyriel:setting:defaultProjectDir:pick')
  },
  zoom: {
    // Current zoom of this window, as { level, percent }.
    get: () => ipcRenderer.invoke('nyriel:zoom:get'),
    // Synchronous zoom factor (1 = 100%). Coordinate math needs it in the
    // same tick as the event it converts, so no IPC round-trip here.
    factor: () => webFrame.getZoomFactor(),
    setPercent: percent => ipcRenderer.send('nyriel:zoom:set-percent', percent),
    // Fires on every zoom change, including the Ctrl/Cmd +/-/0 shortcuts,
    // so the settings UI can stay in sync with the keyboard.
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nyriel:zoom:changed', listener)

      return () => ipcRenderer.removeListener('nyriel:zoom:changed', listener)
    }
  },
  revealLogs: () => ipcRenderer.invoke('nyriel:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('nyriel:logs:recent'),
  // Fire-and-forget: persists a renderer error-boundary catch (with component
  // stack) to desktop.log so crashes survive the window (#79428).
  reportRendererError: report => ipcRenderer.send('nyriel:logs:renderer-error', report),
  readDir: dirPath => ipcRenderer.invoke('nyriel:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('nyriel:fs:gitRoot', startPath),
  revealPath: targetPath => ipcRenderer.invoke('nyriel:fs:reveal', targetPath),
  openDir: dirPath => ipcRenderer.invoke('nyriel:fs:openDir', dirPath),
  desktopPluginsRoot: () => ipcRenderer.invoke('nyriel:fs:desktopPluginsRoot'),
  logsRoot: () => ipcRenderer.invoke('nyriel:fs:logsRoot'),
  agentPluginsRoot: () => ipcRenderer.invoke('nyriel:fs:agentPluginsRoot'),
  renamePath: (targetPath, newName) => ipcRenderer.invoke('nyriel:fs:rename', targetPath, newName),
  writeTextFile: (filePath, content) => ipcRenderer.invoke('nyriel:fs:writeText', filePath, content),
  trashPath: targetPath => ipcRenderer.invoke('nyriel:fs:trash', targetPath),
  git: {
    worktreeList: repoPath => ipcRenderer.invoke('nyriel:git:worktreeList', repoPath),
    worktreeAdd: (repoPath, options) => ipcRenderer.invoke('nyriel:git:worktreeAdd', repoPath, options),
    worktreeRemove: (repoPath, worktreePath, options) =>
      ipcRenderer.invoke('nyriel:git:worktreeRemove', repoPath, worktreePath, options),
    branchSwitch: (repoPath, branch) => ipcRenderer.invoke('nyriel:git:branchSwitch', repoPath, branch),
    branchList: repoPath => ipcRenderer.invoke('nyriel:git:branchList', repoPath),
    baseBranchList: repoPath => ipcRenderer.invoke('nyriel:git:baseBranchList', repoPath),
    repoStatus: repoPath => ipcRenderer.invoke('nyriel:git:repoStatus', repoPath),
    fileDiff: (repoPath, filePath) => ipcRenderer.invoke('nyriel:git:fileDiff', repoPath, filePath),
    scanRepos: (roots, options) => ipcRenderer.invoke('nyriel:git:scanRepos', roots, options),
    review: {
      list: (repoPath, scope, baseRef) => ipcRenderer.invoke('nyriel:git:review:list', repoPath, scope, baseRef),
      diff: (repoPath, filePath, scope, baseRef, staged) =>
        ipcRenderer.invoke('nyriel:git:review:diff', repoPath, filePath, scope, baseRef, staged),
      stage: (repoPath, filePath) => ipcRenderer.invoke('nyriel:git:review:stage', repoPath, filePath),
      unstage: (repoPath, filePath) => ipcRenderer.invoke('nyriel:git:review:unstage', repoPath, filePath),
      revert: (repoPath, filePath) => ipcRenderer.invoke('nyriel:git:review:revert', repoPath, filePath),
      revParse: (repoPath, ref) => ipcRenderer.invoke('nyriel:git:review:revParse', repoPath, ref),
      commit: (repoPath, message, push) => ipcRenderer.invoke('nyriel:git:review:commit', repoPath, message, push),
      commitContext: repoPath => ipcRenderer.invoke('nyriel:git:review:commitContext', repoPath),
      push: repoPath => ipcRenderer.invoke('nyriel:git:review:push', repoPath),
      shipInfo: repoPath => ipcRenderer.invoke('nyriel:git:review:shipInfo', repoPath),
      prList: (repoPath, branches, numbers) =>
        ipcRenderer.invoke('nyriel:git:review:prList', repoPath, branches, numbers),
      fetchPrComment: (repoPath, url) => ipcRenderer.invoke('nyriel:git:review:fetchPrComment', repoPath, url),
      createPr: repoPath => ipcRenderer.invoke('nyriel:git:review:createPr', repoPath)
    }
  },
  terminal: {
    cwd: id => ipcRenderer.invoke('nyriel:terminal:cwd', id),
    dispose: id => ipcRenderer.invoke('nyriel:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('nyriel:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('nyriel:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('nyriel:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `nyriel:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `nyriel:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('nyriel:close-preview-requested', listener)

    return () => ipcRenderer.removeListener('nyriel:close-preview-requested', listener)
  },
  onPreviewNav: callback => {
    const listener = (_event, command) => callback(command)
    ipcRenderer.on('nyriel:preview-nav', listener)

    return () => ipcRenderer.removeListener('nyriel:preview-nav', listener)
  },
  onOpenFolderRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('nyriel:open-folder-requested', listener)

    return () => ipcRenderer.removeListener('nyriel:open-folder-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('nyriel:open-updates', listener)

    return () => ipcRenderer.removeListener('nyriel:open-updates', listener)
  },
  onDeepLink: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nyriel:deep-link', listener)

    return () => ipcRenderer.removeListener('nyriel:deep-link', listener)
  },
  signalDeepLinkReady: () => ipcRenderer.invoke('nyriel:deep-link-ready'),
  probePluginRepo: payload => ipcRenderer.invoke('nyriel:plugin:probe', payload),
  installDesktopPlugin: payload => ipcRenderer.invoke('nyriel:plugin:installDesktop', payload),
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nyriel:window-state-changed', listener)

    return () => ipcRenderer.removeListener('nyriel:window-state-changed', listener)
  },
  onFocusSession: callback => {
    const listener = (_event, sessionId) => callback(sessionId)
    ipcRenderer.on('nyriel:focus-session', listener)

    return () => ipcRenderer.removeListener('nyriel:focus-session', listener)
  },
  onNotificationAction: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nyriel:notification-action', listener)

    return () => ipcRenderer.removeListener('nyriel:notification-action', listener)
  },
  onNotificationActivate: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nyriel:notification-activate', listener)

    return () => ipcRenderer.removeListener('nyriel:notification-activate', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nyriel:preview-file-changed', listener)

    return () => ipcRenderer.removeListener('nyriel:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nyriel:backend-exit', listener)

    return () => ipcRenderer.removeListener('nyriel:backend-exit', listener)
  },
  // Soft gateway-mode apply finished tearing down the primary backend. Renderer
  // should wipe session lists + re-dial without a window reload.
  onConnectionApplied: callback => {
    const listener = () => callback()
    ipcRenderer.on('nyriel:connection:applied', listener)

    return () => ipcRenderer.removeListener('nyriel:connection:applied', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('nyriel:power-resume', listener)

    return () => ipcRenderer.removeListener('nyriel:power-resume', listener)
  },
  // AC ↔ battery transitions; renderers slow their backstop polls on battery.
  getOnBattery: () => ipcRenderer.invoke('nyriel:power-battery:get'),
  onBatteryChanged: callback => {
    const listener = (_event, onBattery) => callback(Boolean(onBattery))
    ipcRenderer.on('nyriel:power-battery', listener)

    return () => ipcRenderer.removeListener('nyriel:power-battery', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nyriel:boot-progress', listener)

    return () => ipcRenderer.removeListener('nyriel:boot-progress', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.ts (apps/desktop/electron/bootstrap-runner.ts).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('nyriel:bootstrap:get'),
  continueBootstrapLocal: () => ipcRenderer.invoke('nyriel:bootstrap:continue-local'),
  resetBootstrap: () => ipcRenderer.invoke('nyriel:bootstrap:reset'),
  repairBootstrap: () => ipcRenderer.invoke('nyriel:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('nyriel:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('nyriel:bootstrap:event', listener)

    return () => ipcRenderer.removeListener('nyriel:bootstrap:event', listener)
  },
  getVersion: () => ipcRenderer.invoke('nyriel:version'),
  getRemoteDisplayReason: () => ipcRenderer.invoke('nyriel:get-remote-display-reason'),
  uninstall: {
    summary: () => ipcRenderer.invoke('nyriel:uninstall:summary'),
    run: mode => ipcRenderer.invoke('nyriel:uninstall:run', { mode })
  },
  updates: {
    check: () => ipcRenderer.invoke('nyriel:updates:check'),
    apply: opts => ipcRenderer.invoke('nyriel:updates:apply', opts),
    getBranch: () => ipcRenderer.invoke('nyriel:updates:branch:get'),
    setBranch: name => ipcRenderer.invoke('nyriel:updates:branch:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('nyriel:updates:progress', listener)

      return () => ipcRenderer.removeListener('nyriel:updates:progress', listener)
    }
  },
  themes: {
    fetchMarketplace: id => ipcRenderer.invoke('nyriel:vscode-theme:fetch', id),
    searchMarketplace: query => ipcRenderer.invoke('nyriel:vscode-theme:search', query)
  },
  // Find-in-page (Ctrl/Cmd+F): delegates to Electron's
  // webContents.findInPage on the IPC sender's window so a Cmd+F pressed
  // in a secondary session window searches THAT window, not the primary.
  // `onFoundInPage` returns the unsubscribe fn; the renderer wires it via
  // `initFindInPageListener` in store/find-in-page.ts and tears it down
  // when the FindBar unmounts.
  findInPage: (query, options) => ipcRenderer.invoke('nyriel:find-in-page', query, options),
  stopFindInPage: () => ipcRenderer.invoke('nyriel:stop-find-in-page'),
  onFoundInPage: callback => {
    const listener = (_event, result) => callback(result)
    ipcRenderer.on('nyriel:found-in-page', listener)

    return () => ipcRenderer.removeListener('nyriel:found-in-page', listener)
  },
  // Main-process `before-input-event` forwards Ctrl/Cmd+F here so renderer
  // can open the FindBar even when the GTK compositor has already grabbed
  // the chord at the windowing layer (#81727).
  onOpenFindBarRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('nyriel:open-find-bar', listener)

    return () => ipcRenderer.removeListener('nyriel:open-find-bar', listener)
  }
})
