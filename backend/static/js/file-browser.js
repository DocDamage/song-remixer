import { state, ACCEPTED_AUDIO_EXTENSIONS } from './state.js';
import * as ui from './ui.js';

export function initFileBrowser(options) {
    const { switchTab, assignDroppedFile } = options;

    const browseFolderBtn = document.getElementById('browse-folder-btn');
    const folderFallbackInput = document.getElementById('folder-fallback-input');
    const fileBrowserSearch = document.getElementById('file-browser-search');
    const fileTreeEl = document.getElementById('file-tree');
    const sidebarPlayerAudio = document.getElementById('sidebar-player-audio');
    const sidebarPlayerName = document.getElementById('sidebar-player-name');
    const sidebarPlayerActions = document.getElementById('sidebar-player-actions');

    let browserRoots = [];
    let selectedTreeNode = null;
    let previewObjectUrl = null;
    let searchQuery = '';

    function isAudioFileName(name) {
        return ACCEPTED_AUDIO_EXTENSIONS.test(name);
    }

    async function browseFolder() {
        if ('showDirectoryPicker' in window) {
            try {
                const handle = await window.showDirectoryPicker();
                addPickerRoot(handle);
            } catch (err) {
                if (err.name !== 'AbortError') ui.showError('Could not open folder: ' + err.message);
            }
        } else {
            folderFallbackInput.click();
        }
    }

    function addPickerRoot(handle) {
        const root = {
            name: handle.name,
            kind: 'directory',
            children: null,
            expanded: true,
            handle: handle,
            file: null,
            path: handle.name,
            source: 'picker'
        };
        browserRoots.push(root);
        renderBrowser();
    }

    function addFileListRoot(files) {
        if (!files.length) return;
        const tree = buildTreeFromPaths(files);
        browserRoots.push(tree);
        renderBrowser();
    }

    function buildTreeFromPaths(files) {
        let rootName = 'Folder';
        if (files[0] && files[0].webkitRelativePath) {
            rootName = files[0].webkitRelativePath.split('/')[0];
        }

        const root = {
            name: rootName,
            kind: 'directory',
            children: [],
            expanded: true,
            handle: null,
            file: null,
            path: rootName,
            source: 'filelist'
        };

        for (const file of files) {
            const path = file.webkitRelativePath || file.name;
            const parts = path.split('/');
            let current = root;
            for (let i = 1; i < parts.length; i++) {
                const part = parts[i];
                const isFile = i === parts.length - 1;
                if (isFile) {
                    if (isAudioFileName(part)) {
                        current.children.push({
                            name: part,
                            kind: 'file',
                            children: null,
                            expanded: false,
                            handle: null,
                            file: file,
                            path: path,
                            source: 'filelist'
                        });
                    }
                } else {
                    let child = current.children.find((c) => c.kind === 'directory' && c.name === part);
                    if (!child) {
                        child = {
                            name: part,
                            kind: 'directory',
                            children: [],
                            expanded: false,
                            handle: null,
                            file: null,
                            path: parts.slice(0, i + 1).join('/'),
                            source: 'filelist'
                        };
                        current.children.push(child);
                    }
                    current = child;
                }
            }
        }

        sortTree(root);
        return root;
    }

    function sortTree(node) {
        if (node.kind !== 'directory' || !node.children) return;
        node.children.sort((a, b) => {
            if (a.kind === b.kind) return a.name.localeCompare(b.name);
            return a.kind === 'directory' ? -1 : 1;
        });
        for (const child of node.children) sortTree(child);
    }

    function renderBrowser() {
        fileTreeEl.innerHTML = '';
        if (browserRoots.length === 0) {
            fileTreeEl.innerHTML = '<div class="file-tree-empty">Click "+ Folder" to browse local files</div>';
            return;
        }

        const treeRoot = document.createElement('div');
        treeRoot.className = 'fb-tree';
        for (const root of browserRoots) {
            const el = createNodeElement(root, 0);
            treeRoot.appendChild(el);
        }
        fileTreeEl.appendChild(treeRoot);
    }

    function createNodeElement(node, depth) {
        const container = document.createElement('div');

        if (node.kind === 'directory') {
            const header = document.createElement('div');
            header.className = 'fb-item fb-folder';
            header.style.paddingLeft = `${depth * 12}px`;

            const chevron = document.createElement('span');
            chevron.className = 'fb-chevron' + (node.expanded ? ' is-open' : '');
            chevron.textContent = '▶';

            const icon = document.createElement('span');
            icon.className = 'fb-icon';
            icon.textContent = '📁';

            const name = document.createElement('span');
            name.className = 'fb-name';
            name.textContent = node.name;

            header.appendChild(chevron);
            header.appendChild(icon);
            header.appendChild(name);
            container.appendChild(header);

            const childrenContainer = document.createElement('div');
            childrenContainer.className = 'fb-children';
            childrenContainer.style.display = node.expanded ? 'flex' : 'none';
            container.appendChild(childrenContainer);

            header.addEventListener('click', async () => {
                const willExpand = childrenContainer.style.display === 'none';
                if (willExpand && node.children === null && node.handle) {
                    await lazyLoadChildren(node);
                }
                node.expanded = willExpand;
                childrenContainer.style.display = willExpand ? 'flex' : 'none';
                chevron.classList.toggle('is-open', willExpand);
            });

            if (node.expanded) {
                if (node.children === null && node.handle) {
                    lazyLoadChildren(node).then(() => {
                        renderChildren(node, childrenContainer, depth + 1);
                    });
                } else {
                    renderChildren(node, childrenContainer, depth + 1);
                }
            }
        } else {
            const item = document.createElement('div');
            item.className = 'fb-item fb-file';
            item.style.paddingLeft = `${depth * 12}px`;

            const spacer = document.createElement('span');
            spacer.className = 'fb-chevron';
            spacer.style.visibility = 'hidden';
            spacer.textContent = '▶';

            const icon = document.createElement('span');
            icon.className = 'fb-icon';
            icon.textContent = '🎵';

            const name = document.createElement('span');
            name.className = 'fb-name';
            name.textContent = node.name;

            item.appendChild(spacer);
            item.appendChild(icon);
            item.appendChild(name);

            item.addEventListener('click', () => {
                fileTreeEl.querySelectorAll('.fb-item.is-selected').forEach((el) => el.classList.remove('is-selected'));
                item.classList.add('is-selected');
                selectedTreeNode = node;
                previewNode(node);
            });

            container.appendChild(item);
        }

        return container;
    }

    function renderChildren(node, container, depth) {
        container.innerHTML = '';
        if (!node.children || node.children.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'fb-item';
            empty.style.paddingLeft = `${depth * 12}px`;
            empty.style.color = '#444';
            empty.textContent = 'Empty';
            container.appendChild(empty);
            return;
        }

        for (const child of node.children) {
            if (searchQuery && !matchesSearch(child, searchQuery.toLowerCase())) continue;
            const el = createNodeElement(child, depth);
            container.appendChild(el);
        }
    }

    function matchesSearch(node, query) {
        if (node.name.toLowerCase().includes(query)) return true;
        if (node.kind === 'directory' && node.children) {
            return node.children.some((c) => matchesSearch(c, query));
        }
        return false;
    }

    async function lazyLoadChildren(node) {
        if (!node.handle || node.children !== null) return;
        const entries = [];
        try {
            for await (const entry of node.handle.values()) {
                entries.push(entry);
            }
        } catch (err) {
            node.children = [];
            return;
        }

        entries.sort((a, b) => {
            if (a.kind === b.kind) return a.name.localeCompare(b.name);
            return a.kind === 'directory' ? -1 : 1;
        });

        node.children = [];
        for (const entry of entries) {
            if (entry.kind === 'directory') {
                node.children.push({
                    name: entry.name,
                    kind: 'directory',
                    children: null,
                    expanded: false,
                    handle: entry,
                    file: null,
                    path: node.path + '/' + entry.name,
                    source: 'picker'
                });
            } else if (isAudioFileName(entry.name)) {
                node.children.push({
                    name: entry.name,
                    kind: 'file',
                    children: null,
                    expanded: false,
                    handle: entry,
                    file: null,
                    path: node.path + '/' + entry.name,
                    source: 'picker'
                });
            }
        }
    }

    async function previewNode(node) {
        try {
            if (previewObjectUrl) {
                URL.revokeObjectURL(previewObjectUrl);
                previewObjectUrl = null;
            }

            let file = null;
            if (node.source === 'filelist' && node.file) {
                file = node.file;
            } else if (node.source === 'picker' && node.handle) {
                file = await node.handle.getFile();
            }

            if (!file) return;
            previewObjectUrl = URL.createObjectURL(file);
            sidebarPlayerAudio.src = previewObjectUrl;
            sidebarPlayerAudio.load();
            sidebarPlayerName.textContent = file.name;
            sidebarPlayerName.classList.add('has-file');

            try {
                await sidebarPlayerAudio.play();
            } catch (playErr) {
                // Autoplay blocked is OK
            }
        } catch (err) {
            ui.showError('Could not preview file: ' + err.message);
        }
    }

    async function loadSelectedNodeToSlot(slotId) {
        if (!selectedTreeNode) return;
        try {
            let file = null;
            if (selectedTreeNode.source === 'filelist' && selectedTreeNode.file) {
                file = selectedTreeNode.file;
            } else if (selectedTreeNode.source === 'picker' && selectedTreeNode.handle) {
                file = await selectedTreeNode.handle.getFile();
            }
            if (!file) return;

            const input = document.getElementById(slotId);
            const transfer = new DataTransfer();
            transfer.items.add(file);
            input.files = transfer.files;
            input.dispatchEvent(new Event('change', { bubbles: true }));
            if (slotId === 'stem-track') {
                switchTab('stems');
            } else {
                switchTab('remix');
            }
        } catch (err) {
            ui.showError('Could not load file: ' + err.message);
        }
    }

    // Event Listeners
    if (browseFolderBtn) {
        browseFolderBtn.addEventListener('click', browseFolder);
    }

    if (folderFallbackInput) {
        folderFallbackInput.addEventListener('change', (e) => {
            const files = Array.from(e.target.files);
            if (files.length) addFileListRoot(files);
            folderFallbackInput.value = '';
        });
    }

    if (fileBrowserSearch) {
        fileBrowserSearch.addEventListener('input', (e) => {
            searchQuery = e.target.value.trim();
            renderBrowser();
        });
    }

    if (sidebarPlayerActions) {
        sidebarPlayerActions.querySelectorAll('[data-load-slot]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const slot = btn.dataset.loadSlot;
                loadSelectedNodeToSlot(slot);
            });
        });
    }
}
