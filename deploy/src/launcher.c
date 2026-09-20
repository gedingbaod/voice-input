/*
 * VoiceInput.app 启动器（编译为真正的 Mach-O，供 ad-hoc 签名）。
 *
 * 读 Contents/Resources/launcher.conf（三行）：
 *   1: python 可执行文件绝对路径
 *   2: 项目根目录（client 包所在）
 *   3: 日志文件绝对路径
 * 然后 chdir → 重定向 stdout/stderr 到日志 → exec python -u -m client
 *
 * 配置外置的意义：改 conf 不需要重新编译/重新签名，
 * 辅助功能授权绑定的是签名，签名不变授权就一直有效。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <libgen.h>

int main(void) {
    char self[4096];
    uint32_t size = sizeof(self);
    if (_NSGetExecutablePath(self, &size) != 0) { fprintf(stderr, "path too long\n"); return 1; }

    /* self = .../VoiceInput.app/Contents/MacOS/VoiceInput → conf 在 ../Resources/ */
    char dirbuf[4096];
    strncpy(dirbuf, self, sizeof(dirbuf) - 1);
    dirbuf[sizeof(dirbuf) - 1] = 0;
    const char *dir = dirname(dirbuf);  /* 用返回值；不要依赖原地修改 */
    char conf_path[4096];
    snprintf(conf_path, sizeof(conf_path), "%s/../Resources/launcher.conf", dir);

    FILE *f = fopen(conf_path, "r");
    if (!f) { fprintf(stderr, "no launcher.conf at %s\n", conf_path); return 1; }
    char python[4096] = {0}, root[4096] = {0}, logpath[4096] = {0};
    if (!fgets(python, sizeof(python), f) ||
        !fgets(root, sizeof(root), f) ||
        !fgets(logpath, sizeof(logpath), f)) {
        fprintf(stderr, "launcher.conf needs 3 lines: python, root, log\n");
        return 1;
    }
    fclose(f);
    python[strcspn(python, "\n")] = 0;
    root[strcspn(root, "\n")] = 0;
    logpath[strcspn(logpath, "\n")] = 0;

    if (chdir(root) != 0) { perror("chdir"); return 1; }

    int fd = open(logpath, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd >= 0) { dup2(fd, 1); dup2(fd, 2); if (fd > 2) close(fd); }

    char *const av[] = {python, "-u", "-m", "client", NULL};
    execv(python, av);
    perror("execv");  /* 只有失败才返回 */
    return 1;
}
