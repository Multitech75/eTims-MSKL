### Kenya Tims Intigration

 

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/Multitech75/eTims-MSKL.git
bench --site your-site.local install-app mtl_tims
bench migrate
```

### Requirements
```bash
Install Qrcode
cd /home/frappeuser/frappe-bench
./env/bin/pip install qrcode[pil]
```

### License

mit
