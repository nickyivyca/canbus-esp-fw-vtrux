/*
 * The file and W7 calls /upload/car_data.json's handler makes, faked for a host
 * build (shared/wican W16). Gen-inhibit tester. Included after fake_idf.h, so
 * the handler's fopen, fwrite, fclose and unlink are these: the file is kept in
 * memory and every call is counted in the case's record (fake_S).
 */
#ifndef FAKE_FILE_H
#define FAKE_FILE_H

#include "fake_idf.h"
#include "fake_engine.h"

typedef struct fake_file fake_file_t;

fake_file_t *fake_fopen(const char *path, const char *mode);
size_t fake_fwrite(const void *p, size_t size, size_t n, fake_file_t *f);
int fake_fclose(fake_file_t *f);
int fake_unlink(const char *path);

#define FILE   fake_file_t
#define fopen  fake_fopen
#define fwrite fake_fwrite
#define fclose fake_fclose
#define unlink fake_unlink

/* SPEC W7's refusal while another owner holds the bus: never refused here. */
bool flash_write_refused(httpd_req_t *req, const char *what);

#endif
