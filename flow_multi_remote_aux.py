import random
import string


def getRandomString(length):
    # choose from all lowercase letter
    letters = string.ascii_lowercase
    resultStr = ''.join(random.choice(letters) for i in range(length))
    
    return resultStr
