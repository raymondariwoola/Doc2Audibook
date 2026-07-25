mod runtime;

use runtime::{
    generate_bootstrap_token, navigation_allowed, parse_ready_frame, sidecar_url,
    startup_navigation_allowed, DesktopPaths, PendingBootstrap, ReadyFrame,
};
use std::fs::{self, OpenOptions};
use std::io::{Error as IoError, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::Duration;
use tauri::{
    LogicalSize, Manager, RunEvent, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

struct SidecarProcess {
    child: Mutex<Option<CommandChild>>,
    terminated: Arc<(Mutex<bool>, Condvar)>,
    shutting_down: AtomicBool,
}

impl Default for SidecarProcess {
    fn default() -> Self {
        Self {
            child: Mutex::new(None),
            terminated: Arc::new((Mutex::new(false), Condvar::new())),
            shutting_down: AtomicBool::new(false),
        }
    }
}

#[derive(Default)]
struct DesktopBootstrapState {
    pending: Mutex<Option<PendingBootstrap>>,
}

#[tauri::command]
fn take_desktop_bootstrap_token(
    window: WebviewWindow,
    state: State<'_, DesktopBootstrapState>,
) -> Result<Option<String>, String> {
    if window.label() != "main" {
        return Err("desktop bootstrap was requested from an unknown window".into());
    }
    let current_url = window
        .url()
        .map_err(|_| "desktop bootstrap origin could not be verified".to_string())?;
    let mut guard = state
        .pending
        .lock()
        .map_err(|_| "desktop bootstrap lock was poisoned".to_string())?;
    let pending = guard
        .as_mut()
        .ok_or_else(|| "desktop bootstrap is not available".to_string())?;
    pending.take_for(&current_url).map_err(str::to_string)
}

fn boxed_error(message: impl Into<String>) -> Box<dyn std::error::Error> {
    Box::new(IoError::other(message.into()))
}

fn append_sidecar_log(path: &std::path::Path, label: &str, bytes: &[u8]) {
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let text = String::from_utf8_lossy(bytes);
        let _ = writeln!(file, "[{label}] {}", text.trim_end());
    }
}

fn resolve_paths(app: &tauri::App) -> Result<DesktopPaths, Box<dyn std::error::Error>> {
    let paths = DesktopPaths::new(
        &app.path().app_local_data_dir()?,
        &app.path().app_cache_dir()?,
        &app.path().app_log_dir()?,
        &app.path().resource_dir()?,
    );
    for directory in [&paths.library, &paths.previews, &paths.logs, &paths.models] {
        fs::create_dir_all(directory)?;
    }
    for required in [
        &paths.frontend.join("index.html"),
        &paths.ffmpeg,
        &paths.ffprobe,
    ] {
        if !required.is_file() {
            return Err(boxed_error(format!(
                "required desktop resource is missing: {}",
                required.display()
            )));
        }
    }
    Ok(paths)
}

fn spawn_sidecar(
    app: &tauri::App,
    paths: &DesktopPaths,
) -> Result<(ReadyFrame, String), Box<dyn std::error::Error>> {
    let state = app.state::<SidecarProcess>();
    state.shutting_down.store(false, Ordering::SeqCst);
    if let Ok(mut terminated) = state.terminated.0.lock() {
        *terminated = false;
    }
    let token = generate_bootstrap_token();
    let piper_models = paths.models.join("piper");
    let kokoro_models = paths.models.join("kokoro");
    fs::create_dir_all(&piper_models)?;
    fs::create_dir_all(&kokoro_models)?;

    let command = app
        .shell()
        .sidecar("doc2audiobook-sidecar")?
        .args(["--port", "0"])
        .env("DOC2AUDIOBOOK_WEB_DATA_DIR", &paths.library)
        .env("DOC2AUDIOBOOK_WEB_PREVIEW_DIR", &paths.previews)
        .env("DOC2AUDIOBOOK_FRONTEND_DIST", &paths.frontend)
        .env("DOC2AUDIOBOOK_LOG_DIR", &paths.logs)
        .env("DOC2AUDIOBOOK_DESKTOP_TOKEN", &token)
        .env("DOC2AUDIOBOOK_FFMPEG", &paths.ffmpeg)
        .env("DOC2AUDIOBOOK_FFPROBE", &paths.ffprobe)
        .env("PIPER_VOICE_DIR", &piper_models)
        .env("KOKORO_MODEL_DIR", &kokoro_models);
    let (mut events, child) = command.spawn()?;
    state
        .child
        .lock()
        .map_err(|_| boxed_error("desktop sidecar lock was poisoned"))?
        .replace(child);

    let (ready_sender, ready_receiver) = mpsc::sync_channel(1);
    let log_path = paths.logs.join("desktop-sidecar.log");
    let app_handle = app.handle().clone();
    let terminated_signal = Arc::clone(&state.terminated);
    tauri::async_runtime::spawn(async move {
        let mut sent_ready = false;
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    append_sidecar_log(&log_path, "stdout", &bytes);
                    if !sent_ready {
                        if let Ok(frame) = parse_ready_frame(&String::from_utf8_lossy(&bytes)) {
                            let _ = ready_sender.send(Ok(frame));
                            sent_ready = true;
                        }
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    append_sidecar_log(&log_path, "stderr", &bytes);
                }
                CommandEvent::Error(message) => {
                    append_sidecar_log(&log_path, "error", message.as_bytes());
                    if !sent_ready {
                        let _ = ready_sender.send(Err(message));
                        sent_ready = true;
                    }
                }
                CommandEvent::Terminated(payload) => {
                    append_sidecar_log(&log_path, "terminated", format!("{payload:?}").as_bytes());
                    if let Ok(mut terminated) = terminated_signal.0.lock() {
                        *terminated = true;
                        terminated_signal.1.notify_all();
                    }
                    if !sent_ready {
                        let _ =
                            ready_sender.send(Err("sidecar terminated before readiness".into()));
                        sent_ready = true;
                    } else if !app_handle
                        .state::<SidecarProcess>()
                        .shutting_down
                        .load(Ordering::SeqCst)
                    {
                        let exit_handle = app_handle.clone();
                        app_handle
                            .dialog()
                            .message(
                                "The private narration engine stopped unexpectedly. \
                                 Your saved jobs remain on this computer. Restart \
                                 Doc2Audiobook to resume.",
                            )
                            .title("Doc2Audiobook engine stopped")
                            .kind(MessageDialogKind::Error)
                            .show(move |_| exit_handle.exit(1));
                    }
                }
                _ => {}
            }
        }
    });

    match ready_receiver.recv_timeout(Duration::from_secs(45)) {
        Ok(Ok(frame)) => Ok((frame, token)),
        Ok(Err(message)) => {
            shutdown_sidecar(app.handle());
            Err(boxed_error(message))
        }
        Err(_) => {
            shutdown_sidecar(app.handle());
            Err(boxed_error(
                "desktop narration engine did not become ready within 45 seconds",
            ))
        }
    }
}

fn shutdown_sidecar(app: &tauri::AppHandle) {
    let state = app.state::<SidecarProcess>();
    state.shutting_down.store(true, Ordering::SeqCst);
    let child = state.child.lock().ok().and_then(|mut guard| guard.take());
    if let Some(mut child) = child {
        let _ = child.write(b"{\"command\":\"shutdown\"}\n");
        let (terminated, signal) = &*state.terminated;
        let stopped = terminated
            .lock()
            .ok()
            .and_then(|guard| {
                signal
                    .wait_timeout_while(guard, Duration::from_secs(10), |done| !*done)
                    .ok()
            })
            .is_some_and(|result| *result.0);
        if !stopped {
            let _ = child.kill();
        }
    }
}

fn build_startup_window(
    app: &tauri::App,
    ready_frame: Arc<Mutex<Option<ReadyFrame>>>,
) -> tauri::Result<WebviewWindow> {
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Starting Doc2Audiobook")
        .inner_size(720.0, 480.0)
        .min_inner_size(640.0, 420.0)
        .resizable(false)
        .center()
        .on_navigation(move |next| {
            startup_navigation_allowed(next)
                || ready_frame
                    .lock()
                    .ok()
                    .and_then(|frame| frame.clone())
                    .is_some_and(|frame| navigation_allowed(next, &frame))
        })
        .build()
}

fn show_startup_error(window: &WebviewWindow, message: &str) {
    let safe_message = serde_json::to_string(message)
        .unwrap_or_else(|_| "\"See the desktop log for details.\"".into());
    let _ = window.set_title("Doc2Audiobook could not start");
    let _ = window.eval(format!(
        "document.getElementById('startup-title').textContent = \
         'Doc2Audiobook could not start'; \
         document.getElementById('startup-message').textContent = {safe_message};"
    ));
    let _ = window.show();
    let _ = window.set_focus();
}

fn activate_main_window(
    window: &WebviewWindow,
    frame: &ReadyFrame,
) -> Result<(), Box<dyn std::error::Error>> {
    let url = sidecar_url(frame)?;
    window.set_title("Doc2Audiobook")?;
    window.set_resizable(true)?;
    window.set_size(LogicalSize::new(1280.0, 860.0))?;
    window.set_min_size(Some(LogicalSize::new(960.0, 680.0)))?;
    window.center()?;
    window.navigate(url)?;
    window.show()?;
    window.set_focus()?;
    Ok(())
}

fn start_desktop(
    app: &tauri::App,
    window: &WebviewWindow,
    ready_frame: &Arc<Mutex<Option<ReadyFrame>>>,
) -> Result<(), Box<dyn std::error::Error>> {
    let paths = resolve_paths(app)?;
    let (ready, token) = spawn_sidecar(app, &paths)?;
    *ready_frame
        .lock()
        .map_err(|_| boxed_error("desktop navigation lock was poisoned"))? = Some(ready.clone());
    app.state::<DesktopBootstrapState>()
        .pending
        .lock()
        .map_err(|_| boxed_error("desktop bootstrap lock was poisoned"))?
        .replace(PendingBootstrap::new(ready.clone(), token));
    activate_main_window(window, &ready)
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(SidecarProcess::default())
        .manage(DesktopBootstrapState::default())
        .invoke_handler(tauri::generate_handler![take_desktop_bootstrap_token])
        .setup(|app| {
            let ready_frame = Arc::new(Mutex::new(None));
            let window = build_startup_window(app, Arc::clone(&ready_frame))?;
            if let Err(error) = start_desktop(app, &window, &ready_frame) {
                eprintln!("desktop startup failed: {error}");
                if let Ok(mut pending) = app.state::<DesktopBootstrapState>().pending.lock() {
                    pending.take();
                }
                shutdown_sidecar(app.handle());
                show_startup_error(
                    &window,
                    &format!(
                        "{error} Review the desktop logs, then restart after \
                         correcting the reported resource or engine problem."
                    ),
                );
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Doc2Audiobook desktop shell");

    app.run(|handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            shutdown_sidecar(handle);
        }
    });
}
