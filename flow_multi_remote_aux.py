import random
import string


def getRandomString(length):
    # choose from all lowercase letter
    letters = string.ascii_lowercase
    resultStr = ''.join(random.choice(letters) for i in range(length))
    
    return resultStr

def convertLitersByMinute2LitersByHour(valu2Convert):
    return valu2Convert * 60.0

def convertLitersByMinute2m3ByHour(valu2Convert):
    return valu2Convert * 60.0 / 1000.0

def convertLitersByMinute2GallonsByMinute(valu2Convert):
    return valu2Convert * 0.264172052

def convertLitersByMinute2GallonsByHour(valu2Convert):
    return valu2Convert * 0.264172052 * 60.0

def convertLiters2m3(valu2Convert):
    return valu2Convert / 1000.0

def convertLiters2Gal(valu2Convert):
    return valu2Convert * 0.264172052
