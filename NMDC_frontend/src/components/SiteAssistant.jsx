import {useEffect, useRef, useState} from "react";
import {ArrowUpRight, MessageCircle, Send, X} from "lucide-react";
import {Link} from "react-router-dom";
import {useAuth} from "../auth/AuthContext";
import {getVisibleBays} from "../config/cameras";
import {answerQuestion} from "../services/assistant";
import "./SiteAssistant.css";

const adminPrompts = ["Engagements today", "Live system performance", "Show recording history", "Check PoE mappings"];

function AssistantPanel({user}) {
  const prompts = ["Which cameras are online?", `Open ${getVisibleBays(user.license_id)[0].id.replace("Bay-", " ")}`, ...(user.role === "admin" ? adminPrompts : [])];
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const request = useRef(null);
  const launcher = useRef(null);
  const input = useRef(null);
  const history = useRef(null);

  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => { if (open) input.current?.focus(); }, [open]);
  useEffect(() => {
    if (history.current) history.current.scrollTop = history.current.scrollHeight;
  }, [messages, open, busy]);

  function close() {
    setOpen(false);
    launcher.current?.focus();
  }

  async function ask(question) {
    const text = question.trim();
    if (!text || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setDraft("");
    setMessages(previous => [...previous, {text, question: true}]);
    setBusy(true);
    try {
      const answer = await answerQuestion(text, user, controller.signal);
      if (!controller.signal.aborted) setMessages(previous => [...previous, answer]);
    } catch {
      if (!controller.signal.aborted) setMessages(previous => [...previous, {
        text: "That data is unavailable right now. Check your connection or try again.",
      }]);
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return (
    <div className="site-assistant" onKeyDown={event => { if (open && event.key === "Escape") close(); }}>
      {open && <section className="assistant-panel" id="site-assistant-panel" aria-labelledby="site-assistant-title">
        <header>
          <h2 id="site-assistant-title"><MessageCircle size={20} aria-hidden="true" /> NMDC Assistant</h2>
          <button type="button" onClick={close} aria-label="Close chat"><X size={20} /></button>
        </header>
        <div className="assistant-history" ref={history} role="log" aria-label="Chat messages">
          <p className="assistant-message">Ask about the monitoring app or choose a question below.</p>
          {messages.map(({text, question, route}, index) => <div className="assistant-message" data-question={question} key={index}>
            <p>{text}</p>
            {route && <Link to={route[0]} onClick={close}>Open {route[1]} <ArrowUpRight size={14} aria-hidden="true" /></Link>}
          </div>)}
          {busy && <p className="assistant-message">Checking...</p>}
        </div>
        {!messages.length && <div className="assistant-prompts">
          {prompts.map(question => <button type="button" key={question} onClick={() => { ask(question); input.current?.focus(); }}>{question}</button>)}
        </div>}
        <form onSubmit={event => { event.preventDefault(); ask(draft); }}>
          <input ref={input} aria-label="Ask NMDC Assistant" placeholder="Ask about a bay or dashboard..." value={draft} onChange={event => setDraft(event.target.value)} maxLength={300} />
          <button type="submit" disabled={busy || !draft.trim()} aria-label="Send question"><Send size={18} /></button>
        </form>
      </section>}
      <button ref={launcher} type="button" className="assistant-launcher" aria-label={open ? "Close NMDC Assistant" : "Open NMDC Assistant"} aria-controls={open ? "site-assistant-panel" : undefined} aria-expanded={open} onClick={() => open ? close() : setOpen(true)}>
        {open ? <X size={22} /> : <span className="assistant-robot" aria-hidden="true">
          <span className="assistant-robot-antennae" />
          <span className="assistant-robot-earpads" />
          <span className="assistant-robot-head"><span className="assistant-robot-visor" /></span>
          <span className="assistant-robot-mic" />
        </span>}
      </button>
    </div>
  );
}

export default function SiteAssistant() {
  const {user} = useAuth();
  return user ? <AssistantPanel key={`${user.license_id}:${user.role}`} user={user} /> : null;
}
