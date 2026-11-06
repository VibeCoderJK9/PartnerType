// PartnerType — local AI chat for admins and partners.
// Every dynamic value is rendered as text; writes require a server-backed card click.

// A translated string, escaped for the fixed markup below: translations are
// data, so none of them may add markup of its own.
function escText(key) {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return String(App.t(key)).replace(/[&<>"']/g, ch => map[ch]);
}

document.addEventListener('DOMContentLoaded', async () => {
    let session = null;
    try {
        session = JSON.parse(sessionStorage.getItem('ph_session'));
    } catch (error) {
        session = null;
    }
    if (!session) {
        try {
            session = typeof AdminShell !== 'undefined'
                ? await AdminShell.init()
                : await Ui.rehydrateSession(ApiClient);
        } catch { return; }
    }
    if (!session || !['admin', 'partner'].includes(session.role)) return;

    const isAdmin = session.role === 'admin';
    const container = document.createElement('div');
    container.id = 'agent-container';
    container.innerHTML = `
        <section class="agent-dock" id="agent-panel" aria-label="${escText('agent_title')}">
            <header class="agent-chat-header">
                <div class="agent-header-title">
                    <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"></path>
                    </svg>
                    <span id="agent-title">${escText('agent_title')}</span>
                    <span class="agent-status-dot is-checking" id="agent-status" title="${escText('agent_model_checking')}"></span>
                </div>
                <div class="agent-header-actions">
                    <button class="agent-header-btn" id="agent-clear" type="button" title="${escText('agent_new_chat')}" aria-label="${escText('agent_new_chat')}">
                        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><polyline points="1 4 1 10 7 10"></polyline><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path></svg>
                    </button>
                    <button class="agent-header-btn" id="agent-expand" type="button" title="${escText('agent_expand')}" aria-label="${escText('agent_expand_window')}">
                        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><polyline points="15 3 21 3 21 9"></polyline><polyline points="9 21 3 21 3 15"></polyline><line x1="21" y1="3" x2="14" y2="10"></line><line x1="3" y1="21" x2="10" y2="14"></line></svg>
                    </button>
                    <button class="agent-header-btn" id="agent-close" type="button" title="${escText('agent_close')}" aria-label="${escText('agent_close_window')}">
                        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                    </button>
                </div>
            </header>
            <div class="agent-chat-messages" id="agent-messages" aria-live="polite"></div>
            <div class="agent-quick-actions" id="agent-quick-actions"></div>
            <p class="agent-ai-notice" id="agent-ai-notice">${escText('agent_ai_notice')}</p>
            <div class="agent-chat-input">
                <input type="text" id="agent-input" maxlength="4000" autocomplete="off" aria-label="${escText('agent_message_label')}">
                <button id="agent-send" type="button" title="${escText('agent_send')}" aria-label="${escText('agent_send_message')}">
                    <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>
                </button>
            </div>
        </section>
    `;
    document.body.appendChild(container);

    /*
       Stripe-style docking: the icon sits next to the avatar, the chat opens
       on the right and stays open across screens until it is closed.

       Two things that needed solving rather than styling:

       * The button belongs beside the avatar in the header, not floating over
         the page. It is injected here rather than added to fourteen HTML files.

       * "stays with me even when I change the view" cannot be literal in a
         multi-page app -- every navigation is a new document. What persists is
         the STATE (localStorage) and the TRANSCRIPT, and the transcript is read
         back from the server, which already holds the conversation the model
         remembers. Mirroring it into browser storage would let the two drift.
    */
    const CHAT_STORAGE_KEY = 'ph_chat';

    function readChatPreference() {
        try {
            return localStorage.getItem(CHAT_STORAGE_KEY) === 'open';
        } catch (error) {
            return false;
        }
    }

    function storeChatPreference(open) {
        try { localStorage.setItem(CHAT_STORAGE_KEY, open ? 'open' : 'closed'); }
        catch (error) { /* private window: the dock still works, it just forgets */ }
    }

    const toggleBtn = document.createElement('button');
    toggleBtn.type = 'button';
    toggleBtn.id = 'agent-toggle';
    toggleBtn.className = 'agent-header-toggle';
    toggleBtn.title = App.t('agent_title');
    toggleBtn.setAttribute('aria-label', App.t('agent_title'));
    toggleBtn.setAttribute('aria-expanded', 'false');
    // Built through the generated icon set rather than an innerHTML string: this
    // file is on the security audit's sink list, and a real element costs
    // nothing here.
    Icons.into(toggleBtn, 'bot', { size: 18 });

    const avatar = document.getElementById('header-avatar');
    if (avatar && avatar.parentNode) {
        avatar.parentNode.insertBefore(toggleBtn, avatar);
    } else {
        // No header on this screen: the dock is still reachable, just not from
        // a button that has nowhere to sit.
        (document.querySelector('.header-right') || container).appendChild(toggleBtn);
    }

    const panel = document.getElementById('agent-panel');
    const closeBtn = document.getElementById('agent-close');
    const clearBtn = document.getElementById('agent-clear');
    const expandBtn = document.getElementById('agent-expand');
    const panelEl = document.getElementById('agent-panel');
    const messages = document.getElementById('agent-messages');
    const input = document.getElementById('agent-input');
    const sendBtn = document.getElementById('agent-send');
    const quickActs = document.getElementById('agent-quick-actions');
    const statusDot = document.getElementById('agent-status');
    // The offline hint lives on the header button.
    const pulse = toggleBtn;
    let isExpanded = false;

    input.placeholder = isAdmin
        ? App.t('agent_placeholder_admin')
        : App.t('agent_placeholder_partner');

    function addMessage(text, type, icon) {
        const msg = document.createElement('div');
        msg.className = `agent-msg ${type}`;
        // AI Act art. 50(2): text the model wrote is marked as AI-generated in a
        // machine-readable way, not only by the bubble's colour.
        if (type === 'ai') msg.dataset.generatedBy = 'ai';
        const content = document.createElement('div');
        content.className = 'agent-msg-content';
        if (icon) {
            content.classList.add('agent-msg-alert');
            Icons.label(content, icon, String(text ?? ''), { size: 16 });
        } else {
            content.textContent = String(text ?? '');
        }
        msg.appendChild(content);
        messages.appendChild(msg);
        messages.scrollTop = messages.scrollHeight;
        if (type === 'user') quickActs.hidden = true;
        return msg;
    }

    function welcome() {
        const abilities = App.t(isAdmin ? 'agent_abilities_admin' : 'agent_abilities_partner');
        addMessage(App.t('agent_greeting', { abilities }), 'ai');
    }

    function addQuickAction(icon, labelKey, prompt) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'agent-quick-btn';
        button.dataset.icon = icon;
        button.dataset.labelKey = labelKey;
        Icons.label(button, icon, App.t(labelKey), { size: 14 });
        button.dataset.prompt = prompt;
        quickActs.appendChild(button);
    }

    if (isAdmin) {
        addQuickAction('bar-chart-3', 'agent_quick_stats', App.t('agent_chip_stats'));
        addQuickAction('sparkles', 'agent_quick_new_leads', App.t('agent_chip_new_leads'));
        addQuickAction('users', 'agent_quick_partners', App.t('agent_chip_active_partners'));
    } else {
        addQuickAction('bar-chart-3', 'agent_quick_my_stats', App.t('agent_chip_my_stats'));
        addQuickAction('clipboard-list', 'agent_quick_my_leads', App.t('agent_chip_my_latest'));
        addQuickAction('plus', 'agent_quick_prepare_lead', App.t('agent_chip_new_lead'));
    }
    welcome();

    /*
       Redraw the conversation the model still remembers.

       This is what makes the dock survive a navigation: the server keeps the
       transcript per user for 30 minutes (api/agent.py), so on a fresh document
       we read it back rather than starting the reader off with a greeting and an
       assistant that clearly remembers a conversation they can no longer see.

       Pending action cards are deliberately NOT restored. Their confirmation
       tokens are short-lived and single-use by design (api/agent_actions.py); a
       card redrawn from history would be a button that either does nothing or,
       worse, looks like a fresh proposal. If the reader still wants the change,
       they ask again.
    */
    async function restoreHistory() {
        if (!window.ApiClient || typeof ApiClient.get !== 'function') return;
        try {
            const data = await ApiClient.get('/api/agent/history');
            const history = Array.isArray(data.messages) ? data.messages : [];
            if (!history.length) return;
            messages.replaceChildren();
            quickActs.hidden = true;
            history.forEach((message) => {
                addMessage(message.content, message.role === 'user' ? 'user' : 'ai');
            });
        } catch (error) {
            // Keep the greeting. A failed read is not worth an error message in
            // a panel the reader may not even have opened on purpose.
        }
    }

    function removeAssistant() {
        document.documentElement.dataset.chat = 'closed';
        toggleBtn.remove();
        container.remove();
    }

    async function refreshStatus() {
        if (!window.ApiClient || typeof ApiClient.get !== 'function') return;
        try {
            const status = await ApiClient.get('/api/agent/status');
            if (status.disabled) {
                // An instance with the assistant turned off: nothing to
                // show, rather than a button that only ever says "offline".
                removeAssistant();
                return;
            }
            statusDot.classList.remove('is-checking', 'is-offline');
            pulse.classList.remove('is-offline');
            if (status.available) {
                statusDot.classList.add('is-online');
                statusDot.title = `${status.message} Model: ${status.model}`;
            } else {
                statusDot.classList.add('is-offline');
                pulse.classList.add('is-offline');
                statusDot.title = status.message || App.t('agent_model_unavailable');
            }
        } catch (error) {
            statusDot.classList.remove('is-checking', 'is-online');
            statusDot.classList.add('is-offline');
            pulse.classList.add('is-offline');
            statusDot.title = App.t('agent_model_check_failed');
        }
    }

    // `data-chat` on the root is what narrows the centre column (css/main.css),
    // so opening the dock is a document-level state, not a class on the panel.
    function setChatOpen(open, { focus = false, remember = true } = {}) {
        document.documentElement.dataset.chat = open ? 'open' : 'closed';
        panel.classList.toggle('open', open);
        toggleBtn.classList.toggle('active', open);
        toggleBtn.setAttribute('aria-expanded', String(open));
        if (remember) storeChatPreference(open);
        if (open && focus) input.focus();
    }

    toggleBtn.addEventListener('click', () => {
        setChatOpen(document.documentElement.dataset.chat !== 'open', { focus: true });
    });

    closeBtn.addEventListener('click', () => setChatOpen(false));

    expandBtn.addEventListener('click', () => {
        isExpanded = !isExpanded;
        panelEl.classList.toggle('expanded', isExpanded);
        document.documentElement.dataset.chatWide = String(isExpanded);
    });

    clearBtn.addEventListener('click', async () => {
        messages.replaceChildren();
        quickActs.hidden = false;
        welcome();
        try {
            await ApiClient.post('/api/agent/reset', {});
        } catch (error) {
            addMessage(App.t('agent_clear_failed'), 'ai');
        }
    });

    quickActs.addEventListener('click', (event) => {
        const button = event.target.closest('.agent-quick-btn');
        if (!button) return;
        input.value = button.dataset.prompt || '';
        sendMessage();
    });

    function showTyping() {
        const msg = document.createElement('div');
        msg.className = 'agent-msg ai agent-typing-msg';
        const indicator = document.createElement('div');
        indicator.className = 'agent-typing';
        indicator.append(document.createElement('span'), document.createElement('span'), document.createElement('span'));
        msg.appendChild(indicator);
        messages.appendChild(msg);
        messages.scrollTop = messages.scrollHeight;
        return msg;
    }

    function retirePendingCards() {
        messages.querySelectorAll('.agent-action-card[data-pending="true"]').forEach((card) => {
            card.dataset.pending = 'false';
            card.classList.add('is-expired');
            card.querySelectorAll('button').forEach((button) => { button.disabled = true; });
            const state = card.querySelector('.agent-action-state');
            if (state) state.textContent = App.t('agent_superseded');
        });
    }

    /*
       A translate_labels confirmation can come back
       "partial" -- canonical writers commit independently, so a batch of
       bonus/tier/product/stage/field names can fail partway through. The
       card must never look like a plain "Operation complete" success when
       that happened: this renders which names actually saved, which one
       failed (or could not be confirmed), and which were never attempted --
       every value through textContent, since these are producer-authored
       strings. `translations` is `result.result.translations` from
       api/agent_actions.py::_confirm_translate_labels.
    */
    function translationItemLabel(item) {
        return String(item.polish ?? item.english ?? item.id ?? '');
    }

    function renderTranslationOutcome(card, translations) {
        const box = document.createElement('div');
        box.className = 'agent-action-outcome';

        function row(labelKey, text) {
            const line = document.createElement('div');
            line.className = 'agent-action-outcome-row';
            const label = document.createElement('strong');
            label.textContent = App.t(labelKey);
            line.append(label, document.createTextNode(` ${text}`));
            box.appendChild(line);
        }

        if (translations.written.length) {
            row(
                'agent_translations_written_label',
                `${translations.written.length}: ${translations.written.map(translationItemLabel).join(', ')}`,
            );
        }
        if (translations.failed) {
            row(
                'agent_translations_failed_label',
                `${translationItemLabel(translations.failed)} — ${translations.failed.message || ''}`,
            );
        }
        if (translations.outcome_unknown) {
            row(
                'agent_translations_unknown_label',
                `${translationItemLabel(translations.outcome_unknown)} — ${translations.outcome_unknown.message || ''}`,
            );
        }
        if (translations.not_attempted.length) {
            row(
                'agent_translations_skipped_label',
                translations.not_attempted.map(translationItemLabel).join(', '),
            );
        }

        if (translations.failed || translations.outcome_unknown || translations.not_attempted.length) {
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'agent-action-retry';
            retry.textContent = App.t('agent_translations_retry');
            retry.addEventListener('click', () => {
                input.value = App.t('agent_translations_retry_prompt');
                sendMessage();
            });
            box.appendChild(retry);
        }

        card.appendChild(box);
    }

    function addActionCard(action) {
        if (!action || typeof action.id !== 'string') return;
        retirePendingCards();

        const card = document.createElement('article');
        card.className = `agent-action-card${action.risk === 'financial' ? ' is-financial' : ''}`;
        card.dataset.pending = 'true';

        const eyebrow = document.createElement('div');
        eyebrow.className = 'agent-action-eyebrow';
        eyebrow.textContent = action.risk === 'financial'
            ? App.t('agent_financial_confirmation')
            : App.t('agent_data_confirmation');

        const title = document.createElement('div');
        title.className = 'agent-action-title';
        title.textContent = String(action.title || App.t('agent_confirm_operation'));
        card.append(eyebrow, title);

        const summary = document.createElement('dl');
        summary.className = 'agent-action-summary';
        if (Array.isArray(action.summary)) {
            action.summary.slice(0, 20).forEach((item) => {
                if (!item || typeof item !== 'object') return;
                const label = document.createElement('dt');
                const value = document.createElement('dd');
                label.textContent = String(item.label || App.t('agent_data'));
                value.textContent = String(item.value ?? '—');
                summary.append(label, value);
            });
        }
        card.appendChild(summary);

        const state = document.createElement('div');
        state.className = 'agent-action-state';
        state.textContent = App.t('agent_nothing_saved');

        const actions = document.createElement('div');
        actions.className = 'agent-action-buttons';
        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className = 'agent-action-cancel';
        cancel.textContent = App.t('agent_cancel');
        const confirm = document.createElement('button');
        confirm.type = 'button';
        confirm.className = 'agent-action-confirm';
        confirm.textContent = String(action.confirm_label || App.t('agent_confirm'));
        actions.append(cancel, confirm);
        card.append(state, actions);
        messages.appendChild(card);
        messages.scrollTop = messages.scrollHeight;

        async function submitDecision(kind) {
            if (card.dataset.pending !== 'true') return;
            confirm.disabled = true;
            cancel.disabled = true;
            state.textContent = App.t(kind === 'confirm' ? 'agent_executing' : 'agent_cancelling');
            try {
                const result = await ApiClient.post(
                    `/api/agent/actions/${encodeURIComponent(action.id)}/${kind}`,
                    {},
                );
                card.dataset.pending = 'false';
                const translations = result.result && result.result.translations;
                if (kind === 'confirm' && translations && translations.partial) {
                    // Never claim "Operation complete" over a batch that
                    // only partly saved -- see renderTranslationOutcome.
                    card.classList.add('is-partial');
                    state.textContent = App.t('agent_partially_saved');
                    renderTranslationOutcome(card, translations);
                } else {
                    card.classList.add(kind === 'confirm' ? 'is-complete' : 'is-cancelled');
                    state.textContent = App.t(kind === 'confirm' ? 'agent_executed' : 'agent_cancelled');
                }
                addMessage(result.response || App.t('agent_done'), 'ai');
            } catch (error) {
                confirm.disabled = false;
                cancel.disabled = false;
                state.textContent = App.t('agent_failed_prefix', {
                    message: error.message || App.t('agent_server_error'),
                });
            }
        }

        confirm.addEventListener('click', () => submitDecision('confirm'));
        cancel.addEventListener('click', () => submitDecision('cancel'));
    }

    async function sendMessage() {
        const text = input.value.trim();
        if (!text || input.disabled) return;

        addMessage(text, 'user');
        input.value = '';
        input.disabled = true;
        sendBtn.disabled = true;
        const typingMsg = showTyping();

        try {
            // The model is told which language to answer in: an English screen
            // with a Polish answer in the chat is just one more untranslated string.
            const data = await ApiClient.post('/api/agent/chat', { prompt: text, lang: App.lang },
                // A model turn can take minutes; the server bounds it (210 s).
                { timeout: 220000 });
            if (data.response) addMessage(data.response, 'ai');
            else if (data.error) addMessage(data.error, 'ai', 'triangle-alert');
            if (data.action) addActionCard(data.action);
        } catch (error) {
            addMessage(error.message || App.t('agent_no_model_connection'), 'ai', 'triangle-alert');
            refreshStatus();
        } finally {
            typingMsg.remove();
            input.disabled = false;
            sendBtn.disabled = false;
            input.focus();
        }
    }

    sendBtn.addEventListener('click', sendMessage);
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });

    // Restore the dock to however the user left it, on every screen. Written
    // without remembering, so reopening a page does not itself count as a
    // decision about the dock.
    setChatOpen(readChatPreference(), { remember: false });

    App.onRerender(() => {
        const title = document.getElementById('agent-title');
        if (title) title.textContent = App.t('agent_title');
        const notice = document.getElementById('agent-ai-notice');
        if (notice) notice.textContent = App.t('agent_ai_notice');
        panel.setAttribute('aria-label', App.t('agent_title'));
        toggleBtn.title = App.t('agent_title');
        toggleBtn.setAttribute('aria-label', App.t('agent_title'));
        input.placeholder = App.t(isAdmin ? 'agent_placeholder_admin' : 'agent_placeholder_partner');
        [
            ['agent-clear', 'agent_new_chat', 'agent_new_chat'],
            ['agent-expand', 'agent_expand', 'agent_expand_window'],
            ['agent-close', 'agent_close', 'agent_close_window'],
            ['agent-send', 'agent_send', 'agent_send_message'],
        ].forEach(([id, titleKey, ariaKey]) => {
            const button = document.getElementById(id);
            if (button) { button.title = App.t(titleKey); button.setAttribute('aria-label', App.t(ariaKey)); }
        });
        input.setAttribute('aria-label', App.t('agent_message_label'));
        quickActs.querySelectorAll('.agent-quick-btn').forEach((button) => {
            const labelKey = button.dataset.labelKey;
            const icon = button.dataset.icon;
            if (labelKey && icon) Icons.label(button, icon, App.t(labelKey), { size: 14 });
            const promptKey = isAdmin
                ? { agent_quick_stats: 'agent_chip_stats', agent_quick_new_leads: 'agent_chip_new_leads', agent_quick_partners: 'agent_chip_active_partners' }[labelKey]
                : { agent_quick_my_stats: 'agent_chip_my_stats', agent_quick_my_leads: 'agent_chip_my_latest', agent_quick_prepare_lead: 'agent_chip_new_lead' }[labelKey];
            if (promptKey) button.dataset.prompt = App.t(promptKey);
        });
        if (statusDot.classList.contains('is-checking')) statusDot.title = App.t('agent_model_checking');
    });

    refreshStatus();
    void restoreHistory();
});
