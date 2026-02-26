class PiStatusbar < Formula
  desc "Pi macOS status bar app with local daemon and session controls"
  homepage "https://github.com/jademind/pi-statusbar"
  url "https://github.com/jademind/pi-statusbar/archive/refs/tags/v0.1.10.tar.gz"
  sha256 "d5d3bb8b883314369f431caa104c622db53c6035698c05f84291f860867d495c"
  version "0.1.10"
  license "MIT"
  head "https://github.com/jademind/pi-statusbar.git", branch: "main"

  depends_on :macos
  depends_on "python@3.12"
  depends_on "swift"

  def install
    libexec.install Dir["*"]
    ENV["SWIFTPM_DISABLE_SANDBOX"] = "1"

    cd libexec do
      system "swift", "build", "--disable-sandbox", "-c", "release", "--product", "PiStatusBar"
      bin.install ".build/release/PiStatusBar"
    end

    (bin/"statusdctl").write_env_script libexec/"daemon/statusdctl", PI_STATUSBAR_ROOT: libexec
    (bin/"statusd-service").write_env_script libexec/"daemon/statusd-service", PI_STATUSBAR_ROOT: libexec
    (bin/"statusbar-app-service").write_env_script libexec/"daemon/statusbar-app-service", PI_STATUSBAR_ROOT: libexec
    (bin/"pi-statusbar").write_env_script libexec/"daemon/pi-statusbar", PI_STATUSBAR_ROOT: libexec
  end

  service do
    run [
      Formula["python@3.12"].opt_bin/"python3.12",
      opt_libexec/"daemon/pi_statusd.py"
    ]
    keep_alive true
    run_type :immediate
    working_dir var
    log_path var/"log/pi-statusd.log"
    error_log_path var/"log/pi-statusd.log"
  end

  def caveats
    <<~EOS
      Quick setup (start now + enable at login):
        pi-statusbar enable

      Start now only (no login autostart):
        pi-statusbar enable --login no

      Stop now:
        pi-statusbar stop

      Stop and remove login autostart:
        pi-statusbar stop --remove yes

      Verify:
        pi-statusbar status
    EOS
  end

  test do
    output = shell_output("#{bin}/statusdctl status 2>&1", 1)
    assert_match "pi-statusd", output
  end
end
