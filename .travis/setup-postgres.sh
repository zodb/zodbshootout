pip install -U -e ".[postgresql]"
pip install -U pg8000 psycopg2cffi
python -c 'import psycopg2cffi'
psql -U postgres -c "CREATE USER relstoragetest WITH PASSWORD 'relstoragetest';"
psql -U postgres -c "CREATE DATABASE relstoragetest OWNER relstoragetest;"
psql -U postgres -c "CREATE DATABASE relstoragetest2 OWNER relstoragetest;"
psql -U postgres -c "CREATE DATABASE relstoragetest_hf OWNER relstoragetest;"
psql -U postgres -c "CREATE DATABASE relstoragetest2_hf OWNER relstoragetest;"
