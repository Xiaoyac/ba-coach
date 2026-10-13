"use client";

import { useState } from "react";
import BirthdayPicker from "@/components/BirthdayPicker";
import {
  AuthError,
  login,
  register,
  requestPasswordReset,
  type Account,
} from "@/lib/auth";
import { EnsoMark } from "@/components/icons";

/**
 * Mirrors the ENUM/SET values on `user_profile` (see backend
 * `app/schemas.py`, which declares the same lists as Literals). Only the
 * fields that change what the coach may safely suggest are asked here —
 * physical limits and movement taboos gate every activity recommendation, and
 * communication preference sets the tone from the first reply. Everything
 * else on that table is left for the conversation to fill in.
 */
const COMMUNICATION_PREFERENCES = [
  "直接明了",
  "温柔引导",
  "理性分析",
  "轻松幽默",
] as const;

const PHYSICAL_CONDITIONS = [
  "膝关节损伤", "腰背酸痛", "慢性疼痛", "易疲劳", "睡眠障碍",
  "偏头痛", "哮喘", "眩晕", "鼻炎", "术后恢复期",
] as const;

const LIVING_STATUS = ["独居", "和家人", "和朋友", "和恋人"] as const;

const BEHAVIOR_TABOOS = [
  "不能剧烈运动", "不能久站", "不能晒太阳", "怕吵闹", "怕人多",
  "怕拥挤闭塞的地方", "不坐公共交通",
] as const;

type Mode = "login" | "register" | "forgot";

export default function AuthScreen({
  onAuthenticated,
}: {
  onAuthenticated: (account: Account) => void;
}) {
  const [mode, setMode] = useState<Mode>("login");
  const [invitationCode, setInvitationCode] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [nickname, setNickname] = useState("");
  const [birthDate, setBirthDate] = useState("");
  const [living, setLiving] = useState<string | null>(null);
  const [communication, setCommunication] = useState<string | null>(null);
  const [conditions, setConditions] = useState<string[]>([]);
  const [taboos, setTaboos] = useState<string[]>([]);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function toggle(list: string[], value: string): string[] {
    return list.includes(value)
      ? list.filter((v) => v !== value)
      : [...list, value];
  }

  function switchMode(next: Mode) {
    setMode(next);
    // Carry the username across — someone who just failed to log in and is
    // switching to register has almost certainly typed it already. The
    // password is deliberately not carried.
    setPassword("");
    setError(null);
    setNotice(null);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (mode === "register" && !birthDate) {
      setError("请填写出生日期，不能留空。");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "forgot") {
        const message = await requestPasswordReset(email);
        setNotice(message);
        setBusy(false);
        return;
      }
      const account =
        mode === "login"
          ? await login(username, password)
          : await register({
              invitation_code: invitationCode.trim(),
              username,
              password,
              email,
              nickname: nickname.trim(),
              // Only the user's explicit answer is sent, with no fallback.
              birth_date: birthDate,
              living_status: living,
              communication_preference: communication,
              physical_condition: conditions,
              behavior_taboo: taboos,
            });
      onAuthenticated(account);
    } catch (err) {
      setError(
        err instanceof AuthError
          ? err.message
          : "连接不上服务器，请确认后端正在运行",
      );
      setBusy(false);
    }
  }

  const registering = mode === "register";
  const forgot = mode === "forgot";

  return (
    // No <main> here: this renders *inside* the one in app/page.tsx, and
    // nesting landmarks makes the page ambiguous to a screen reader.
    // `overflow-y-auto` because the register form is taller than a short
    // viewport once the preference chips are showing.
    <div className="zen-page-enter relative z-10 flex w-full justify-center overflow-y-auto p-1 sm:p-3">
      {/* Centered with `m-auto`, deliberately NOT the parent's
          `items-center`. When a flex child is taller than its scrolling
          container, `align-items: center` overflows it equally in both
          directions — and the part that overflows past the *start* edge
          cannot be scrolled back to, so the top of this card (the mark and
          the title) was being clipped on the taller register form. `auto`
          margins only take the space that is actually free, so the child
          pins to the top once it no longer fits and the whole card stays
          reachable. */}
      <div className="auth-shell default-style:m-auto default-style:grid default-style:w-full default-style:max-w-6xl default-style:overflow-hidden default-style:rounded-[32px] default-style:border default-style:border-line-strong default-style:backdrop-blur-2xl default-style:lg:min-h-[min(720px,calc(100dvh-5rem))] default-style:lg:grid-cols-[1.08fr_0.92fr] simple:m-auto simple:grid simple:w-full simple:max-w-[1040px] simple:overflow-hidden simple:lg:min-h-[min(720px,calc(100dvh-5rem))] simple:lg:grid-cols-[1.08fr_0.92fr]">
        <section className="auth-story default-style:hidden default-style:flex-col default-style:justify-between default-style:overflow-hidden default-style:border-r default-style:border-line default-style:p-10 default-style:lg:flex default-style:xl:p-14 simple:hidden simple:flex-col simple:justify-between simple:overflow-hidden simple:p-10 simple:lg:flex simple:xl:p-14">
          <div>
            <div className="flex items-center gap-3">
              <span className="default-style:grid default-style:h-11 default-style:w-11 default-style:place-items-center default-style:rounded-2xl default-style:border default-style:border-accent-edge default-style:bg-accent-wash default-style:text-accent default-style:depth-float simple:grid simple:h-11 simple:w-11 simple:place-items-center simple:rounded-xl simple:text-accent">
                <EnsoMark className="h-6 w-6" />
              </span>
              <div>
                <p className="text-sm font-medium tracking-[0.08em] text-ink">BA COACH</p>
                <p className="mt-0.5 text-xs text-ink-faint">行为激活 · 从很小的一步开始</p>
              </div>
            </div>

            <div className="mt-20 max-w-[34rem]">
              <p className="default-style:eyebrow simple:hidden">YOUR NEXT SMALL STEP</p>
              <h1 className="display-title mt-5 text-[clamp(2.8rem,4.6vw,4.7rem)] leading-[1.03] text-ink">
                把很难的一天，<br />拆成下一小步。
              </h1>
              <p className="mt-6 max-w-md text-[1rem] leading-8 text-ink-muted">
                不急着要求自己变好。我们先一起看清发生了什么，再找到今天真正做得到的行动。
              </p>
            </div>
          </div>

          <div className="grid gap-3 xl:grid-cols-3">
            {[
              ["01", "说出现在", "从你的真实感受开始"],
              ["02", "看见循环", "理解情绪与行动的联系"],
              ["03", "迈出一步", "留下可完成的小目标"],
            ].map(([step, title, copy]) => (
              <div key={step} className="default-style:rounded-2xl default-style:border default-style:border-line default-style:bg-panel/55 default-style:p-4 default-style:backdrop-blur-xl simple:py-4 ">
                <span className="default-style:number-chip simple:text-xs simple:tabular-nums simple:text-ink-faint">{step}</span>
                <p className="mt-4 text-sm font-medium text-ink">{title}</p>
                <p className="mt-1 text-xs leading-5 text-ink-faint">{copy}</p>
              </div>
            ))}
          </div>
        </section>

        <div className="auth-form-panel flex min-w-0 items-center px-5 py-7 sm:px-9 lg:px-12 xl:px-16">
          <div className="w-full">
            <div className="mb-7 flex items-center gap-3 lg:hidden">
              <span className="default-style:grid default-style:h-10 default-style:w-10 default-style:place-items-center default-style:rounded-2xl default-style:border default-style:border-accent-edge default-style:bg-accent-wash default-style:text-accent simple:grid simple:h-10 simple:w-10 simple:place-items-center simple:rounded-xl simple:text-accent">
                <EnsoMark className="h-5 w-5" />
              </span>
              <div>
                <h1 className="text-[0.95rem] font-medium tracking-wide text-ink">BA行为激活教练</h1>
                <p className="mt-0.5 text-[0.72rem] text-ink-faint">从很小的一步开始</p>
              </div>
            </div>

            <div className="mb-6">
              <p className="default-style:eyebrow simple:hidden">{forgot ? "ACCOUNT RECOVERY" : registering ? "CREATE YOUR SPACE" : "WELCOME BACK"}</p>
              <h2 className="display-title mt-2 text-2xl text-ink sm:text-[1.75rem]">
                {forgot ? "找回你的账号" : registering ? "建立属于你的空间" : "欢迎回来"}
              </h2>
              <p className="mt-2 text-sm leading-6 text-ink-faint">
                {forgot ? "输入已验证邮箱，我们会发送安全的重置入口。" : registering ? "先填写必要信息，其他内容可以之后慢慢补充。" : "继续上一次对话，或者从今天重新开始。"}
              </p>
            </div>

        <form
          onSubmit={handleSubmit}
          className="w-full"
        >
          {/* Mode switch */}
          {!forgot && <div className="default-style:mb-6 default-style:flex default-style:gap-1 default-style:rounded-2xl default-style:border default-style:border-line default-style:bg-raised/80 default-style:p-1.5 simple:mb-6 simple:flex simple:gap-1 simple:rounded-xl simple:bg-raised/60 simple:p-1">
            {(["login", "register"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => switchMode(m)}
                className={`default-style:flex-1 default-style:rounded-xl default-style:py-2.5 default-style:text-[0.82rem] default-style:font-medium default-style:transition-colors default-style:duration-300 simple:flex-1 simple:rounded-xl simple:py-2.5 simple:text-[0.82rem] simple:font-medium simple:transition-colors simple:duration-300 ${
                  mode === m
                    ? "default-style:bg-panel default-style:text-ink default-style:depth-float simple:bg-panel simple:text-ink"
                    : "default-style:text-ink-faint default-style:hover:text-ink-muted simple:text-ink-faint simple:hover:text-ink-muted"
                }`}
              >
                {m === "login" ? "登录" : "注册"}
              </button>
            ))}
          </div>}

          {forgot && (
            <div className="mb-5">
              <button
                type="button"
                onClick={() => switchMode("login")}
                className="mb-3 text-[0.75rem] text-ink-faint hover:text-accent"
              >
                ← 返回登录
              </button>
              <p className="text-[0.72rem] leading-relaxed text-ink-faint">
                为保护账号隐私，无论邮箱是否存在，页面都会显示相同结果。
              </p>
            </div>
          )}

          {registering && <Field label="邀请码" hint="必填，请向管理员获取；每码仅可注册一次，长期有效">
            <input required value={invitationCode} onChange={(e) => setInvitationCode(e.target.value)}
              autoComplete="off" autoCapitalize="characters" spellCheck={false} maxLength={64}
              placeholder="粘贴管理员提供的邀请码" className={inputClass} />
          </Field>}

          {!forgot && <Field
            label="登录账号"
            hint={registering ? "仅限 3–32 位英文字母和数字，不会显示给其他用户" : undefined}
          >
            <input
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              minLength={registering ? 3 : 1}
              maxLength={registering ? 32 : 64}
              pattern={registering ? "[A-Za-z0-9]+" : undefined}
              placeholder="例如 quietriver"
              className={inputClass}
            />
          </Field>}

          {!forgot && <Field label="密码" hint={registering ? "至少 8 位" : undefined}>
            <input
              required
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              // Tells a password manager whether to offer a saved credential
              // or a generated one — the wrong value here is why managers
              // sometimes overwrite a login with a new random password.
              autoComplete={registering ? "new-password" : "current-password"}
              minLength={registering ? 8 : 1}
              maxLength={128}
              className={inputClass}
            />
          </Field>}

          {(registering || forgot) && (
            <Field
              label={forgot ? "注册时填写的邮箱" : "邮箱"}
              hint={registering ? "用于验证身份和忘记密码时找回账号" : undefined}
            >
              <input
                required
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                maxLength={320}
                placeholder="name@example.com"
                className={inputClass}
              />
            </Field>
          )}

          {registering && (
            <>
              <Field label="昵称" hint="用于页面显示，可以与其他用户同名">
                <input
                  required
                  value={nickname}
                  onChange={(e) => setNickname(e.target.value)}
                  maxLength={58}
                  placeholder="例如 小河"
                  className={inputClass}
                />
              </Field>

              <div className="mb-5">
                <p className="mb-1.5 text-[0.78rem] font-medium text-ink-muted">出生日期 <span className="text-[0.68rem] text-ink-faint">必填 · 支持 10–120 岁</span></p>
                <BirthdayPicker value={birthDate} onChange={setBirthDate} disabled={busy} />
              </div>

              <Field label="目前和谁住" optional>
                <div className="flex flex-wrap gap-1.5">
                  {LIVING_STATUS.map((value) => (
                    <Chip
                      key={value}
                      label={value}
                      selected={living === value}
                      onClick={() => setLiving(living === value ? null : value)}
                    />
                  ))}
                </div>
              </Field>

              <Field label="你希望我用什么方式和你说话" optional>
                <div className="flex flex-wrap gap-1.5">
                  {COMMUNICATION_PREFERENCES.map((value) => (
                    <Chip
                      key={value}
                      label={value}
                      selected={communication === value}
                      onClick={() =>
                        setCommunication(communication === value ? null : value)
                      }
                    />
                  ))}
                </div>
              </Field>

              <Field
                label="身体状况"
                hint="这些会影响我建议什么活动，可多选"
                optional
              >
                <div className="flex flex-wrap gap-1.5">
                  {PHYSICAL_CONDITIONS.map((value) => (
                    <Chip
                      key={value}
                      label={value}
                      selected={conditions.includes(value)}
                      onClick={() => setConditions(toggle(conditions, value))}
                    />
                  ))}
                </div>
              </Field>

              <Field label="有什么是你做不了的" hint="可多选" optional>
                <div className="flex flex-wrap gap-1.5">
                  {BEHAVIOR_TABOOS.map((value) => (
                    <Chip
                      key={value}
                      label={value}
                      selected={taboos.includes(value)}
                      onClick={() => setTaboos(toggle(taboos, value))}
                    />
                  ))}
                </div>
              </Field>
            </>
          )}

          {error && (
            <p
              role="alert"
              className="mb-4 rounded-xl bg-alert-wash px-3 py-2.5 text-[0.78rem] leading-relaxed text-alert-ink"
            >
              {error}
            </p>
          )}

          {notice && (
            <p
              role="status"
              className="mb-4 rounded-xl border border-accent-edge bg-accent-wash px-3 py-2.5 text-[0.78rem] leading-relaxed text-accent-ink"
            >
              {notice}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="primary-action w-full rounded-2xl px-4 text-[0.88rem] font-semibold transition-all duration-300 disabled:opacity-50"
          >
            {busy
              ? "请稍候…"
              : forgot
                ? "发送重置链接"
                : registering
                  ? "创建账号"
                  : "登录"}
          </button>

          {mode === "login" && (
            <button
              type="button"
              onClick={() => switchMode("forgot")}
              className="mt-3 w-full text-center text-[0.73rem] text-ink-faint hover:text-accent"
            >
              忘记密码？
            </button>
          )}

          {registering && (
            <>
              {/* Deliberately louder than the disclaimer below it. The
                  register form asks for a fraction of what the profile holds,
                  and someone who does not know the rest exists will assume
                  this is all the coach can ever know about them. */}
              <div className="mt-4 rounded-2xl border border-accent-edge bg-accent-wash px-3.5 py-3">
              <p className="text-[0.76rem] leading-relaxed text-accent-ink">
                这里只问最关键的几项。注册后在右上角
                <span className="mx-1 rounded-md border border-accent-edge px-1.5 py-0.5 text-[0.72rem]">
                  你的名字 › 我的档案
                </span>
                里，还可以补充居住状况、身边可以支持你的人、不想聊的话题、活动偏好和提醒时段
                —— 填得越多，建议越贴合你。这里填的内容之后也都能改。
              </p>
            </div>

              <p className="mt-3 text-[0.7rem] leading-relaxed text-ink-faint">
                这些信息只用来让建议更贴合你。我不能替代医生或心理咨询师，也不会做医学诊断。
              </p>
            </>
          )}
        </form>

            <div className="mt-6 flex items-center gap-2 text-[0.68rem] leading-5 text-ink-faint">
              <span className="h-px flex-1 bg-line" />
              <span className="text-center">你的记录仅用于提供更贴合的支持</span>
              <span className="h-px flex-1 bg-line" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const inputClass =
  "premium-control w-full rounded-2xl border border-line bg-raised/80 px-4 py-3 text-[0.88rem] text-ink outline-none transition-all duration-300 placeholder:text-ink-faint focus:border-accent-edge focus:bg-raised focus:ring-4 focus:ring-accent-wash";

function Field({
  label,
  hint,
  optional = false,
  children,
}: {
  label: string;
  hint?: string;
  optional?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="mb-5 block">
      <span className="mb-1.5 flex items-baseline gap-1.5">
        <span className="text-[0.78rem] font-medium text-ink-muted">{label}</span>
        {optional && <span className="text-[0.68rem] text-ink-faint">选填</span>}
      </span>
      {hint && (
        <span className="mb-1.5 block text-[0.68rem] text-ink-faint">{hint}</span>
      )}
      {children}
    </label>
  );
}

function Chip({
  label,
  selected,
  onClick,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={`min-h-9 rounded-xl border px-3 py-1.5 text-[0.72rem] transition-all duration-300 ${
        selected
          ? "border-accent-edge bg-accent-wash text-accent-ink"
          : "border-line text-ink-faint hover:text-ink-muted"
      }`}
    >
      {label}
    </button>
  );
}
