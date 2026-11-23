/*
 * The in-memory file behind /upload/car_data.json's host build (shared/wican
 * W16). Gen-inhibit tester. fopen(..., "w") truncates, as a real one does;
 * the bytes written, the opens, writes and removals go in the case's record.
 */
#include "fake_file.h"

#include <stdlib.h>

struct fake_file {
    int unused;
};

static struct fake_file THE_FILE;
static long cap;

fake_file_t *fake_fopen(const char *path, const char *mode)
{
    (void)path;
    fake_S.fopens++;
    if (mode[0] == 'w') {
        free(fake_S.file);
        cap = fake_S.img_len + 65536;
        fake_S.file = calloc(1, (size_t)cap);
        fake_S.file_len = 0;
    }
    fake_S.file_open = true;
    return &THE_FILE;
}

size_t fake_fwrite(const void *p, size_t size, size_t n, fake_file_t *f)
{
    (void)f;
    fake_S.fwrites++;
    long bytes = (long)(size * n);
    if (!fake_S.file || fake_S.file_len + bytes > cap) return 0;
    memcpy(fake_S.file + fake_S.file_len, p, (size_t)bytes);
    fake_S.file_len += bytes;
    return n;
}

int fake_fclose(fake_file_t *f)
{
    (void)f;
    fake_S.file_open = false;
    return 0;
}

int fake_unlink(const char *path)
{
    (void)path;
    fake_S.unlinks++;
    return 0;
}

bool flash_write_refused(httpd_req_t *req, const char *what)
{
    (void)req;
    (void)what;
    return false;
}
