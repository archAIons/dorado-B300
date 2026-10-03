import os, tempfile, sqlite3, subprocess, sys, json, unittest
from pathlib import Path


class MigrationTests(unittest.TestCase):
    def test_existing_jobs_survive_settings_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs.sqlite"
            with sqlite3.connect(path) as c:
                c.execute(
                    """CREATE TABLE jobs(id TEXT PRIMARY KEY,run TEXT,filename TEXT,sha256 TEXT,size INTEGER,offset INTEGER,model TEXT,state TEXT,error TEXT,created REAL,attempt TEXT,heartbeat REAL,metrics TEXT,remote_call TEXT,gpu TEXT,UNIQUE(run,sha256,model,gpu))"""
                )
                c.execute(
                    "INSERT INTO jobs VALUES ('old','account:run','reads.pod5','checksum',4,4,'hac','complete',NULL,1,'attempt',1,NULL,NULL,'B300')"
                )
            env = dict(
                os.environ,
                BASECALL_DATA=tmp,
                BASECALL_TOKEN="migration-test-token-long-enough",
            )
            subprocess.run(
                [sys.executable, "-c", "import server"],
                cwd=Path(__file__).parent,
                env=env,
                check=True,
                timeout=20,
            )
            with sqlite3.connect(path) as c:
                row = c.execute("SELECT id,state,attempt,options FROM jobs").fetchone()
                self.assertEqual(row[:3], ("old", "complete", "attempt"))
                self.assertFalse(json.loads(row[3])["qc"])
            subprocess.run(
                [sys.executable, "-c", "import server"],
                cwd=Path(__file__).parent,
                env=env,
                check=True,
                timeout=20,
            )


if __name__ == "__main__":
    unittest.main()
