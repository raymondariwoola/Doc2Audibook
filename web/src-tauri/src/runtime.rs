use serde::Deserialize;
use std::path::{Path, PathBuf};
use url::Url;
use uuid::Uuid;

pub const SIDECAR_PROTOCOL_VERSION: u32 = 1;

pub fn generate_bootstrap_token() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct ReadyFrame {
    pub event: String,
    pub protocol: u32,
    pub host: String,
    pub port: u16,
}

pub fn parse_ready_frame(line: &str) -> Result<ReadyFrame, String> {
    let frame: ReadyFrame =
        serde_json::from_str(line.trim()).map_err(|error| format!("invalid JSON: {error}"))?;
    if frame.event != "ready" {
        return Err("unexpected sidecar event".into());
    }
    if frame.protocol != SIDECAR_PROTOCOL_VERSION {
        return Err(format!("unsupported sidecar protocol {}", frame.protocol));
    }
    if frame.host != "127.0.0.1" {
        return Err("sidecar did not bind the IPv4 loopback interface".into());
    }
    if frame.port == 0 {
        return Err("sidecar returned port zero".into());
    }
    Ok(frame)
}

pub fn sidecar_url(frame: &ReadyFrame) -> Result<Url, url::ParseError> {
    Url::parse(&format!("http://{}:{}/", frame.host, frame.port))
}

pub fn navigation_allowed(url: &Url, frame: &ReadyFrame) -> bool {
    url.scheme() == "http"
        && url.host_str() == Some(frame.host.as_str())
        && url.port() == Some(frame.port)
}

#[derive(Debug)]
pub struct PendingBootstrap {
    frame: ReadyFrame,
    token: Option<String>,
}

impl PendingBootstrap {
    pub fn new(frame: ReadyFrame, token: String) -> Self {
        Self {
            frame,
            token: Some(token),
        }
    }

    pub fn take_for(&mut self, url: &Url) -> Result<Option<String>, &'static str> {
        if !navigation_allowed(url, &self.frame) {
            return Err("desktop bootstrap was requested from an untrusted origin");
        }
        Ok(self.token.take())
    }
}

pub fn startup_navigation_allowed(url: &Url) -> bool {
    (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
        || (matches!(url.scheme(), "http" | "https") && url.host_str() == Some("tauri.localhost"))
}

pub fn platform_tool_name(name: &str) -> String {
    if cfg!(target_os = "windows") {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DesktopPaths {
    pub library: PathBuf,
    pub previews: PathBuf,
    pub logs: PathBuf,
    pub models: PathBuf,
    pub frontend: PathBuf,
    pub ffmpeg: PathBuf,
    pub ffprobe: PathBuf,
}

impl DesktopPaths {
    pub fn new(
        app_local_data: &Path,
        app_cache: &Path,
        app_log: &Path,
        resource_dir: &Path,
    ) -> Self {
        let tools = resource_dir.join("tools");
        Self {
            library: app_local_data.join("library"),
            previews: app_cache.join("previews"),
            logs: app_log.to_path_buf(),
            models: app_local_data.join("models"),
            frontend: resource_dir.join("frontend"),
            ffmpeg: tools.join(platform_tool_name("ffmpeg")),
            ffprobe: tools.join(platform_tool_name("ffprobe")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ready() -> ReadyFrame {
        ReadyFrame {
            event: "ready".into(),
            protocol: SIDECAR_PROTOCOL_VERSION,
            host: "127.0.0.1".into(),
            port: 49152,
        }
    }

    #[test]
    fn parses_only_the_versioned_loopback_ready_frame() {
        let parsed =
            parse_ready_frame(r#"{"event":"ready","protocol":1,"host":"127.0.0.1","port":49152}"#)
                .unwrap();
        assert_eq!(parsed, ready());
        assert!(parse_ready_frame(
            r#"{"event":"ready","protocol":2,"host":"127.0.0.1","port":49152}"#
        )
        .is_err());
        assert!(parse_ready_frame(
            r#"{"event":"ready","protocol":1,"host":"0.0.0.0","port":49152}"#
        )
        .is_err());
    }

    #[test]
    fn sidecar_url_contains_no_bootstrap_secret_and_navigation_is_origin_locked() {
        let frame = ready();
        let url = sidecar_url(&frame).unwrap();
        assert_eq!(url.query(), None);
        assert_eq!(url.fragment(), None);
        assert!(navigation_allowed(&url, &frame));
        assert!(!navigation_allowed(
            &Url::parse("https://example.com/").unwrap(),
            &frame
        ));
        assert!(!navigation_allowed(
            &Url::parse("http://127.0.0.1:49153/").unwrap(),
            &frame
        ));
    }

    #[test]
    fn bootstrap_secret_is_one_use_and_origin_bound() {
        let frame = ready();
        let mut pending = PendingBootstrap::new(frame.clone(), "secret-value".into());
        let hostile = Url::parse("http://127.0.0.1:49153/").unwrap();
        assert!(pending.take_for(&hostile).is_err());

        let trusted = sidecar_url(&frame).unwrap();
        assert_eq!(
            pending.take_for(&trusted).unwrap(),
            Some("secret-value".into())
        );
        assert_eq!(pending.take_for(&trusted).unwrap(), None);
    }

    #[test]
    fn startup_navigation_is_limited_to_the_packaged_shell() {
        assert!(startup_navigation_allowed(
            &Url::parse("tauri://localhost/index.html").unwrap()
        ));
        assert!(startup_navigation_allowed(
            &Url::parse("http://tauri.localhost/index.html").unwrap()
        ));
        assert!(!startup_navigation_allowed(
            &Url::parse("http://127.0.0.1:49152/").unwrap()
        ));
        assert!(!startup_navigation_allowed(
            &Url::parse("https://example.com/").unwrap()
        ));
    }

    #[test]
    fn bootstrap_tokens_have_two_independent_uuid_payloads() {
        let first = generate_bootstrap_token();
        let second = generate_bootstrap_token();
        assert_eq!(first.len(), 64);
        assert!(first.chars().all(|character| character.is_ascii_hexdigit()));
        assert_ne!(first, second);
    }

    #[test]
    fn derives_machine_local_and_resource_paths_without_cwd() {
        let paths = DesktopPaths::new(
            Path::new("C:/Users/Test/AppData/Local/Doc2Audiobook"),
            Path::new("C:/Users/Test/AppData/Local/Doc2Audiobook/cache"),
            Path::new("C:/Users/Test/AppData/Local/Doc2Audiobook/logs"),
            Path::new("C:/Program Files/Doc2Audiobook/resources"),
        );
        assert!(paths.library.ends_with("library"));
        assert!(paths.previews.ends_with("previews"));
        assert!(paths.frontend.ends_with("frontend"));
        assert!(paths.ffmpeg.ends_with(platform_tool_name("ffmpeg")));
    }
}
