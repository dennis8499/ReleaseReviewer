def release_dependencies(ftp, sftp, redis, kafka):
    ftp.upload("invoices")
    sftp.download("settlements")
    redis.set("invoice:status", "ready", ex=300)
    kafka.send("invoice.created", {"amount": 10})
